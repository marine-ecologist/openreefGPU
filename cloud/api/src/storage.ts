export interface StorageEnv {
  STORAGE_PROVIDER?: string;
  GRAPH_TENANT_ID?: string;
  GRAPH_CLIENT_ID?: string;
  GRAPH_CLIENT_SECRET?: string;
  SHAREPOINT_DRIVE_ID?: string;
  SHAREPOINT_JOB_ROOT?: string;
  GOOGLE_CLIENT_ID?: string;
  GOOGLE_CLIENT_SECRET?: string;
  GOOGLE_REFRESH_TOKEN?: string;
  GOOGLE_DRIVE_ROOT_FOLDER_ID?: string;
  GOOGLE_DRIVE_JOB_ROOT?: string;
}

export interface StoredFile {
  name: string;
  size: number;
}

export interface BrowserUpload {
  uploadUrl: string;
  expiresAt: string;
}

export interface Storage {
  readonly provider: 'sharepoint' | 'google-drive';
  readonly publicRoot: string;
  ensureFolder(path: string): Promise<void>;
  createBrowserUpload(
    path: string,
    size: number,
    contentType: string,
  ): Promise<BrowserUpload>;
  listFiles(path: string): Promise<StoredFile[]>;
  listRootFiles(path: string): Promise<StoredFile[]>;
  readJson<T>(path: string): Promise<T>;
  writeJson(path: string, value: unknown): Promise<void>;
  readFile(path: string, range: string | null): Promise<Response>;
}

const GRAPH_ROOT = 'https://graph.microsoft.com/v1.0';
const GOOGLE_DRIVE_ROOT = 'https://www.googleapis.com/drive/v3';
const GOOGLE_UPLOAD_ROOT = 'https://www.googleapis.com/upload/drive/v3';

let cachedGraphToken: { value: string; expiresAt: number } | undefined;
let cachedGoogleToken: { value: string; expiresAt: number } | undefined;

export function storageForEnv(env: StorageEnv): Storage {
  const provider = (env.STORAGE_PROVIDER || 'sharepoint').toLowerCase();
  if (provider === 'google-drive' || provider === 'google') {
    return new GoogleDriveStorage(env);
  }
  if (provider === 'sharepoint') return new SharePointStorage(env);
  throw new StorageError(500, `Unsupported STORAGE_PROVIDER: ${provider}`);
}

class SharePointStorage implements Storage {
  readonly provider = 'sharepoint' as const;
  readonly publicRoot: string;
  private readonly driveId: string;

  constructor(private readonly env: StorageEnv) {
    this.driveId = required(env.SHAREPOINT_DRIVE_ID, 'SHAREPOINT_DRIVE_ID');
    this.publicRoot = env.SHAREPOINT_JOB_ROOT || 'OpenReef/jobs';
  }

  private fullPath(path: string): string {
    return [this.publicRoot, path]
      .filter(Boolean)
      .join('/')
      .replace(/^\/+|\/+$/g, '');
  }

  async ensureFolder(path: string): Promise<void> {
    const parts = this.fullPath(path).split('/').filter(Boolean);
    let current = '';
    for (const part of parts) {
      const parentPath = current;
      current = current ? `${current}/${part}` : part;
      const lookup = await this.response(
        `/drives/${encodeURIComponent(this.driveId)}/root:/${encodeGraphPath(current)}`,
      );
      if (lookup.ok) continue;
      if (lookup.status !== 404) throw await graphError(lookup);
      const parent = parentPath
        ? `/drives/${encodeURIComponent(this.driveId)}/root:/${encodeGraphPath(parentPath)}:/children`
        : `/drives/${encodeURIComponent(this.driveId)}/root/children`;
      await this.request(parent, {
        method: 'POST',
        body: JSON.stringify({
          name: part,
          folder: {},
          '@microsoft.graph.conflictBehavior': 'fail',
        }),
      });
    }
  }

  async createBrowserUpload(
    path: string,
    _size: number,
    _contentType: string,
  ): Promise<BrowserUpload> {
    const name = path.split('/').pop() || '';
    const session = await this.request<{
      uploadUrl: string;
      expirationDateTime: string;
    }>(
      `/drives/${encodeURIComponent(this.driveId)}/root:/${encodeGraphPath(this.fullPath(path))}:/createUploadSession`,
      {
        method: 'POST',
        body: JSON.stringify({
          item: {
            '@microsoft.graph.conflictBehavior': 'fail',
            name,
          },
        }),
      },
    );
    return {
      uploadUrl: session.uploadUrl,
      expiresAt: session.expirationDateTime,
    };
  }

  async listFiles(path: string): Promise<StoredFile[]> {
    return this.listFilesAtPath(this.fullPath(path));
  }

  async listRootFiles(path: string): Promise<StoredFile[]> {
    return this.listFilesAtPath(path.replace(/^\/+|\/+$/g, ''));
  }

  private async listFilesAtPath(path: string): Promise<StoredFile[]> {
    let next = `/drives/${encodeURIComponent(this.driveId)}/root:/${encodeGraphPath(path)}:/children?$select=name,size&$top=200`;
    const items: StoredFile[] = [];
    while (next) {
      const result: {
        value: StoredFile[];
        '@odata.nextLink'?: string;
      } = await this.request(next);
      items.push(...result.value);
      next = result['@odata.nextLink']?.replace(GRAPH_ROOT, '') ?? '';
    }
    return items;
  }

  async readJson<T>(path: string): Promise<T> {
    return this.request<T>(
      `/drives/${encodeURIComponent(this.driveId)}/root:/${encodeGraphPath(this.fullPath(path))}:/content`,
    );
  }

  async writeJson(path: string, value: unknown): Promise<void> {
    await this.request(
      `/drives/${encodeURIComponent(this.driveId)}/root:/${encodeGraphPath(this.fullPath(path))}:/content`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: `${JSON.stringify(value, null, 2)}\n`,
      },
    );
  }

  async readFile(path: string, range: string | null): Promise<Response> {
    const headers = new Headers({
      Authorization: `Bearer ${await this.token()}`,
    });
    if (range) headers.set('Range', range);
    return fetch(
      `${GRAPH_ROOT}/drives/${encodeURIComponent(this.driveId)}/root:/${encodeGraphPath(this.fullPath(path))}:/content`,
      { headers, redirect: 'follow' },
    );
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await this.response(path, init);
    if (!response.ok) throw await graphError(response);
    if (response.status === 204) return undefined as T;
    return response.json<T>();
  }

  private async response(path: string, init: RequestInit = {}) {
    const headers = new Headers(init.headers);
    headers.set('Authorization', `Bearer ${await this.token()}`);
    if (init.body && !headers.has('Content-Type'))
      headers.set('Content-Type', 'application/json');
    return fetch(`${GRAPH_ROOT}${path}`, { ...init, headers });
  }

  private async token(): Promise<string> {
    if (cachedGraphToken && cachedGraphToken.expiresAt > Date.now() + 60_000)
      return cachedGraphToken.value;
    const body = new URLSearchParams({
      client_id: required(this.env.GRAPH_CLIENT_ID, 'GRAPH_CLIENT_ID'),
      client_secret: required(
        this.env.GRAPH_CLIENT_SECRET,
        'GRAPH_CLIENT_SECRET',
      ),
      scope: 'https://graph.microsoft.com/.default',
      grant_type: 'client_credentials',
    });
    const tenantId = required(this.env.GRAPH_TENANT_ID, 'GRAPH_TENANT_ID');
    const response = await fetch(
      `https://login.microsoftonline.com/${encodeURIComponent(tenantId)}/oauth2/v2.0/token`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body,
      },
    );
    const payload = await response.json<{
      access_token?: string;
      expires_in?: number;
      error_description?: string;
    }>();
    if (!response.ok || !payload.access_token) {
      throw new StorageError(
        502,
        payload.error_description || 'Microsoft Graph authentication failed',
      );
    }
    cachedGraphToken = {
      value: payload.access_token,
      expiresAt: Date.now() + (payload.expires_in || 3600) * 1000,
    };
    return cachedGraphToken.value;
  }
}

type GoogleFile = {
  id: string;
  name: string;
  size?: string;
  mimeType?: string;
};

class GoogleDriveStorage implements Storage {
  readonly provider = 'google-drive' as const;
  readonly publicRoot: string;
  private readonly rootFolderId: string;

  constructor(private readonly env: StorageEnv) {
    this.rootFolderId = required(
      env.GOOGLE_DRIVE_ROOT_FOLDER_ID,
      'GOOGLE_DRIVE_ROOT_FOLDER_ID',
    );
    this.publicRoot = env.GOOGLE_DRIVE_JOB_ROOT || 'jobs';
  }

  private fullPath(path: string): string {
    return [this.publicRoot, path]
      .filter(Boolean)
      .join('/')
      .replace(/^\/+|\/+$/g, '');
  }

  async ensureFolder(path: string): Promise<void> {
    await this.resolveFolder(this.fullPath(path), true);
  }

  async createBrowserUpload(
    path: string,
    size: number,
    contentType: string,
  ): Promise<BrowserUpload> {
    const parts = this.fullPath(path).split('/');
    const name = parts.pop();
    if (!name) throw new StorageError(500, 'Upload path has no file name');
    const parentId = await this.resolveFolder(parts.join('/'), false);
    const token = await this.token();
    const response = await fetch(
      `${GOOGLE_UPLOAD_ROOT}/files?uploadType=resumable&fields=id,name,size`,
      {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json; charset=UTF-8',
          'X-Upload-Content-Length': String(size),
          'X-Upload-Content-Type': contentType,
        },
        body: JSON.stringify({ name, parents: [parentId] }),
      },
    );
    if (!response.ok) throw await googleError(response);
    const uploadUrl = response.headers.get('Location');
    if (!uploadUrl)
      throw new StorageError(502, 'Google Drive did not return an upload URL');
    return {
      uploadUrl,
      // Google documents resumable sessions as lasting one week. Leave a
      // margin so clients never assume a nearly expired session is usable.
      expiresAt: new Date(Date.now() + 6 * 24 * 60 * 60 * 1000).toISOString(),
    };
  }

  async listFiles(path: string): Promise<StoredFile[]> {
    return this.listFilesAtPath(this.fullPath(path));
  }

  async listRootFiles(path: string): Promise<StoredFile[]> {
    return this.listFilesAtPath(path.replace(/^\/+|\/+$/g, ''));
  }

  private async listFilesAtPath(path: string): Promise<StoredFile[]> {
    const parentId = await this.resolveFolder(path, false);
    const items: StoredFile[] = [];
    let pageToken = '';
    do {
      const params = new URLSearchParams({
        q: `'${googleQueryValue(parentId)}' in parents and trashed = false`,
        fields: 'nextPageToken,files(id,name,size,mimeType)',
        pageSize: '1000',
      });
      if (pageToken) params.set('pageToken', pageToken);
      const page = await this.request<{
        files?: GoogleFile[];
        nextPageToken?: string;
      }>(`/files?${params.toString()}`);
      for (const file of page.files || []) {
        if (file.mimeType !== 'application/vnd.google-apps.folder') {
          items.push({ name: file.name, size: Number(file.size || 0) });
        }
      }
      pageToken = page.nextPageToken || '';
    } while (pageToken);
    return items;
  }

  async readJson<T>(path: string): Promise<T> {
    const file = await this.resolveFile(this.fullPath(path));
    return this.request<T>(`/files/${encodeURIComponent(file.id)}?alt=media`);
  }

  async writeJson(path: string, value: unknown): Promise<void> {
    const fullPath = this.fullPath(path);
    const parts = fullPath.split('/');
    const name = parts.pop();
    if (!name) throw new StorageError(500, 'JSON path has no file name');
    const parentId = await this.resolveFolder(parts.join('/'), false);
    let file = await this.findChild(parentId, name);
    if (!file) {
      file = await this.request<GoogleFile>('/files?fields=id,name', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json; charset=UTF-8' },
        body: JSON.stringify({ name, parents: [parentId] }),
      });
    }
    const response = await this.response(
      `/files/${encodeURIComponent(file.id)}?uploadType=media`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: `${JSON.stringify(value, null, 2)}\n`,
      },
      true,
    );
    if (!response.ok) throw await googleError(response);
  }

  async readFile(path: string, range: string | null): Promise<Response> {
    const file = await this.resolveFile(this.fullPath(path));
    const headers = new Headers({
      Authorization: `Bearer ${await this.token()}`,
    });
    if (range) headers.set('Range', range);
    return fetch(
      `${GOOGLE_DRIVE_ROOT}/files/${encodeURIComponent(file.id)}?alt=media`,
      { headers, redirect: 'follow' },
    );
  }

  private async resolveFolder(path: string, create: boolean): Promise<string> {
    let parentId = this.rootFolderId;
    for (const part of path.split('/').filter(Boolean)) {
      let child = await this.findChild(
        parentId,
        part,
        'application/vnd.google-apps.folder',
      );
      if (!child && create) {
        child = await this.request<GoogleFile>(
          '/files?fields=id,name,mimeType',
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json; charset=UTF-8' },
            body: JSON.stringify({
              name: part,
              mimeType: 'application/vnd.google-apps.folder',
              parents: [parentId],
            }),
          },
        );
      }
      if (!child)
        throw new StorageError(404, `Google Drive folder not found: ${part}`);
      parentId = child.id;
    }
    return parentId;
  }

  private async resolveFile(path: string): Promise<GoogleFile> {
    const parts = path.split('/');
    const name = parts.pop();
    if (!name) throw new StorageError(404, 'Google Drive file not found');
    const parentId = await this.resolveFolder(parts.join('/'), false);
    const file = await this.findChild(parentId, name);
    if (!file)
      throw new StorageError(404, `Google Drive file not found: ${name}`);
    return file;
  }

  private async findChild(
    parentId: string,
    name: string,
    mimeType?: string,
  ): Promise<GoogleFile | undefined> {
    const clauses = [
      `'${googleQueryValue(parentId)}' in parents`,
      `name = '${googleQueryValue(name)}'`,
      'trashed = false',
    ];
    if (mimeType) clauses.push(`mimeType = '${googleQueryValue(mimeType)}'`);
    const params = new URLSearchParams({
      q: clauses.join(' and '),
      fields: 'files(id,name,size,mimeType)',
      pageSize: '2',
    });
    const result = await this.request<{ files?: GoogleFile[] }>(
      `/files?${params.toString()}`,
    );
    const files = result.files || [];
    if (files.length > 1) {
      throw new StorageError(
        409,
        `Google Drive contains duplicate items named ${name}`,
      );
    }
    return files[0];
  }

  private async request<T>(
    path: string,
    init: RequestInit = {},
    upload = false,
  ): Promise<T> {
    const response = await this.response(path, init, upload);
    if (!response.ok) throw await googleError(response);
    if (response.status === 204) return undefined as T;
    return response.json<T>();
  }

  private async response(
    path: string,
    init: RequestInit = {},
    upload = false,
  ): Promise<Response> {
    const headers = new Headers(init.headers);
    headers.set('Authorization', `Bearer ${await this.token()}`);
    return fetch(`${upload ? GOOGLE_UPLOAD_ROOT : GOOGLE_DRIVE_ROOT}${path}`, {
      ...init,
      headers,
    });
  }

  private async token(): Promise<string> {
    if (cachedGoogleToken && cachedGoogleToken.expiresAt > Date.now() + 60_000)
      return cachedGoogleToken.value;
    const body = new URLSearchParams({
      client_id: required(this.env.GOOGLE_CLIENT_ID, 'GOOGLE_CLIENT_ID'),
      client_secret: required(
        this.env.GOOGLE_CLIENT_SECRET,
        'GOOGLE_CLIENT_SECRET',
      ),
      refresh_token: required(
        this.env.GOOGLE_REFRESH_TOKEN,
        'GOOGLE_REFRESH_TOKEN',
      ),
      grant_type: 'refresh_token',
    });
    const response = await fetch('https://oauth2.googleapis.com/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body,
    });
    const payload = await response.json<{
      access_token?: string;
      expires_in?: number;
      error?: string;
      error_description?: string;
    }>();
    if (!response.ok || !payload.access_token) {
      throw new StorageError(
        502,
        payload.error_description ||
          payload.error ||
          'Google authentication failed',
      );
    }
    cachedGoogleToken = {
      value: payload.access_token,
      expiresAt: Date.now() + (payload.expires_in || 3600) * 1000,
    };
    return cachedGoogleToken.value;
  }
}

function required(value: string | undefined, name: string): string {
  const result = value?.trim();
  if (!result) throw new StorageError(500, `${name} is required`);
  return result;
}

function encodeGraphPath(path: string): string {
  return path.split('/').map(encodeURIComponent).join('/');
}

function googleQueryValue(value: string): string {
  return value.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

async function graphError(response: Response): Promise<StorageError> {
  const body = await response.text();
  return new StorageError(
    response.status === 404 ? 404 : 502,
    `Microsoft Graph error (${response.status}): ${body.slice(0, 300)}`,
  );
}

async function googleError(response: Response): Promise<StorageError> {
  const body = await response.text();
  return new StorageError(
    response.status === 404 ? 404 : response.status === 409 ? 409 : 502,
    `Google Drive error (${response.status}): ${body.slice(0, 300)}`,
  );
}

export class StorageError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}
