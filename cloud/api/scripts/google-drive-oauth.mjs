#!/usr/bin/env node

import { createHash, randomBytes } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';

const credentialsPath = process.argv[2];
if (!credentialsPath) {
  console.error(
    'Usage: node scripts/google-drive-oauth.mjs /path/to/google-oauth-client.json',
  );
  process.exit(1);
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
// drive.file limits OpenReef to files it creates itself. The helper creates
// the root folder, so the API does not need access to the rest of My Drive.
const scope = 'https://www.googleapis.com/auth/drive.file';

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

    const rootFolderId = await ensureOpenReefFolder(tokens.access_token);
    response
      .writeHead(200, { 'Content-Type': 'text/plain; charset=utf-8' })
      .end('OpenReef is connected to Google Drive. You can close this tab.');

    console.log(
      'Google Drive authorization succeeded. Keep these values secret:\n',
    );
    console.log(`GOOGLE_CLIENT_ID=${client.client_id}`);
    console.log(`GOOGLE_CLIENT_SECRET=${client.client_secret}`);
    console.log(`GOOGLE_REFRESH_TOKEN=${tokens.refresh_token}`);
    console.log(`GOOGLE_DRIVE_ROOT_FOLDER_ID=${rootFolderId}`);
    console.log('\nAn OpenReef folder is now available in My Drive.');
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

async function ensureOpenReefFolder(accessToken) {
  const query = new URLSearchParams({
    q: "'root' in parents and name = 'OpenReef' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
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
      'My Drive contains more than one OpenReef folder. Rename the extras and run this helper again.',
    );
  }
  if (found.files?.[0]?.id) return found.files[0].id;

  const createResponse = await fetch(
    'https://www.googleapis.com/drive/v3/files?fields=id,name',
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${accessToken}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        name: 'OpenReef',
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
