#!/usr/bin/env node

import { createHash, randomBytes } from 'node:crypto';
import { chmod, readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';

const args = process.argv.slice(2);
const credentialsPath = args[0];
const folderOption = args.indexOf('--folder');
const rootFolderName =
  folderOption >= 0 ? args[folderOption + 1]?.trim() : 'OpenReef';
const useExistingFolder = args.includes('--existing');
const envFileOption = args.indexOf('--output-env');
const envFilePath =
  envFileOption >= 0 ? args[envFileOption + 1]?.trim() : undefined;
if (!credentialsPath) {
  console.error(
    'Usage: node scripts/google-drive-oauth.mjs /path/to/google-oauth-client.json [--folder NAME --existing] [--output-env PATH]',
  );
  process.exit(1);
}
if (envFileOption >= 0 && !envFilePath) {
  throw new Error('--output-env requires a path.');
}
if (
  !rootFolderName ||
  rootFolderName.length > 120 ||
  /[/\\]/.test(rootFolderName) ||
  containsControlCharacter(rootFolderName)
) {
  throw new Error('The Google Drive root folder name is invalid.');
}

const credentials = JSON.parse(await readFile(credentialsPath, 'utf8'));
const client = credentials.installed || credentials.web;
if (!client?.client_id || !client?.client_secret) {
  throw new Error('The file does not contain Google OAuth client credentials.');
}

const port = 53682;
const redirectUri = `http://127.0.0.1:${port}/oauth2/callback`;
const state = randomBytes(24).toString('base64url');
const verifier = randomBytes(48).toString('base64url');
const challenge = createHash('sha256').update(verifier).digest('base64url');
// drive.file is enough when this helper creates the app folder. Reusing an
// existing My Drive folder requires Drive scope because OAuth alone cannot
// grant drive.file access to a pre-existing folder without Google Picker.
const scope = useExistingFolder
  ? 'https://www.googleapis.com/auth/drive'
  : 'https://www.googleapis.com/auth/drive.file';

const authUrl = new URL('https://accounts.google.com/o/oauth2/v2/auth');
authUrl.search = new URLSearchParams({
  client_id: client.client_id,
  redirect_uri: redirectUri,
  response_type: 'code',
  scope,
  access_type: 'offline',
  prompt: 'consent',
  include_granted_scopes: 'true',
  state,
  code_challenge: challenge,
  code_challenge_method: 'S256',
}).toString();

console.log(
  '\nOpen this address in your browser and sign in with the Gmail account that will own the OpenReef files:\n',
);
console.log(authUrl.toString());
if (useExistingFolder) {
  console.log(
    `\nThis existing-folder setup requests Drive access so OpenReef can use ${rootFolderName}/images.`,
  );
}
console.log('\nWaiting for Google to return to this computer…\n');

const server = createServer(async (request, response) => {
  try {
    const url = new URL(request.url || '/', redirectUri);
    if (url.pathname !== '/oauth2/callback') {
      response.writeHead(404).end('Not found');
      return;
    }
    if (url.searchParams.get('state') !== state) {
      throw new Error('OAuth state did not match. Start the helper again.');
    }
    const providerError = url.searchParams.get('error');
    if (providerError)
      throw new Error(`Google authorization failed: ${providerError}`);
    const code = url.searchParams.get('code');
    if (!code) throw new Error('Google did not return an authorization code.');

    const tokenResponse = await fetch('https://oauth2.googleapis.com/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        client_id: client.client_id,
        client_secret: client.client_secret,
        redirect_uri: redirectUri,
        code,
        code_verifier: verifier,
        grant_type: 'authorization_code',
      }),
    });
    const tokens = await tokenResponse.json();
    if (!tokenResponse.ok || !tokens.access_token || !tokens.refresh_token) {
      throw new Error(
        tokens.error_description ||
          tokens.error ||
          'Google did not return access and refresh tokens.',
      );
    }

    const rootFolderId = await ensureRootFolder(
      tokens.access_token,
      rootFolderName,
      !useExistingFolder,
    );
    response
      .writeHead(200, { 'Content-Type': 'text/plain; charset=utf-8' })
      .end(
        `OpenReef is connected to ${rootFolderName} in Google Drive. You can close this tab.`,
      );

    const envContents = [
      `GOOGLE_CLIENT_ID=${client.client_id}`,
      `GOOGLE_CLIENT_SECRET=${client.client_secret}`,
      `GOOGLE_REFRESH_TOKEN=${tokens.refresh_token}`,
      `GOOGLE_DRIVE_ROOT_FOLDER_ID=${rootFolderId}`,
      '',
    ].join('\n');
    if (envFilePath) {
      await writeFile(envFilePath, envContents, {
        encoding: 'utf8',
        mode: 0o600,
      });
      await chmod(envFilePath, 0o600);
      console.log(
        `Google Drive authorization succeeded. Secrets saved to ${envFilePath}.`,
      );
    } else {
      console.log(
        'Google Drive authorization succeeded. Keep these values secret:\n',
      );
      console.log(envContents.trimEnd());
    }
    console.log(`\nGoogle Drive root: ${rootFolderName}`);
  } catch (error) {
    response
      .writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' })
      .end(`OpenReef authorization failed: ${error.message}`);
    console.error(error);
    process.exitCode = 1;
  } finally {
    server.close();
  }
});

server.listen(port, '127.0.0.1');
setTimeout(
  () => {
    console.error('Authorization timed out. Run the helper again.');
    process.exitCode = 1;
    server.close();
  },
  10 * 60 * 1000,
).unref();

async function ensureRootFolder(accessToken, folderName, createIfMissing) {
  const query = new URLSearchParams({
    q: `'root' in parents and name = '${googleQueryValue(folderName)}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false`,
    fields: 'files(id,name)',
    pageSize: '2',
  });
  const findResponse = await fetch(
    `https://www.googleapis.com/drive/v3/files?${query}`,
    { headers: { Authorization: `Bearer ${accessToken}` } },
  );
  const found = await findResponse.json();
  if (!findResponse.ok) throw new Error(googleError(found));
  if (found.files?.length > 1) {
    throw new Error(
      `My Drive contains more than one ${folderName} folder. Rename the extras and run this helper again.`,
    );
  }
  if (found.files?.[0]?.id) return found.files[0].id;
  if (!createIfMissing) {
    throw new Error(
      `My Drive does not contain an existing ${folderName} folder.`,
    );
  }

  const createResponse = await fetch(
    'https://www.googleapis.com/drive/v3/files?fields=id,name',
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${accessToken}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        name: folderName,
        mimeType: 'application/vnd.google-apps.folder',
        parents: ['root'],
      }),
    },
  );
  const created = await createResponse.json();
  if (!createResponse.ok || !created.id) throw new Error(googleError(created));
  return created.id;
}

function googleError(payload) {
  return payload?.error?.message || 'Google Drive API request failed.';
}

function googleQueryValue(value) {
  return value.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

function containsControlCharacter(value) {
  for (let index = 0; index < value.length; index += 1) {
    if (value.charCodeAt(index) < 32) return true;
  }
  return false;
}
