import { AwsClient } from 'aws4fetch';
import { StorageError, type Storage, type StorageEnv, type StoredFile } from './storage';

export const VIDEO_PART_BYTES = 32 * 1024 * 1024;

function tag(xml: string, name: string): string | undefined {
  return new RegExp(`<${name}>([^<]*)</${name}>`).exec(xml)?.[1]
    ?.replace(/&(?:amp|quot|apos|lt|gt|#34|#x22);/g, entity => ({
      '&amp;': '&', '&quot;': '"', '&#34;': '"', '&#x22;': '"', '&apos;': "'", '&lt;': '<', '&gt;': '>',
    }[entity] ?? entity));
}

const escapeXml = (value: string) => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;');

export class RunpodStorage implements Storage {
  readonly provider = 'runpod' as const;
  readonly publicRoot = 'openreef-jobs';
  private readonly endpoint: URL;
  private readonly client: AwsClient;

  constructor(env: StorageEnv) {
    const { RUNPOD_S3_ENDPOINT: endpoint, RUNPOD_VOLUME_ID: volume,
      RUNPOD_S3_REGION: region, RUNPOD_S3_ACCESS_KEY: accessKeyId,
      RUNPOD_S3_SECRET_KEY: secretAccessKey } = env;
    if (!endpoint || !volume || !region || !accessKeyId || !secretAccessKey)
      throw new StorageError(503, 'Configure the RunPod network volume and S3 credentials');
    this.endpoint = new URL(endpoint);
    if (this.endpoint.protocol !== 'https:' || !this.endpoint.hostname.endsWith('.runpod.io')
        || !/^[a-zA-Z0-9_-]+$/.test(volume)) {
      throw new StorageError(503, 'Invalid RunPod storage endpoint or volume ID');
    }
    this.endpoint.pathname = '/' + volume;
    this.endpoint.search = '';
    this.client = new AwsClient({ accessKeyId, secretAccessKey, region, service: 's3', retries: 0 });
  }

  private request(method: string, path = '', query: Record<string, string> = {}, init: RequestInit = {}) {
    const url = new URL(this.endpoint);
    if (path) url.pathname += '/' + path.split('/').map(encodeURIComponent).join('/');
    url.search = new URLSearchParams(query).toString();
    return this.client.fetch(url, { ...init, method, redirect: 'manual' });
  }

  private async checked(response: Promise<Response>) {
    const result = await response;
    if (!result.ok) {
      await result.body?.cancel();
      throw new StorageError(result.status === 404 ? 404 : 502, `RunPod storage returned HTTP ${result.status}`);
    }
    return result;
  }

  async ensureFolder() { /* RunPod creates the parent path on object writes. */ }

  async createBrowserUpload(): Promise<never> {
    throw new StorageError(400, 'Choose the stereo-video upload workflow');
  }

  async listRootFiles(): Promise<never> {
    throw new StorageError(400, 'Choose the stereo-video upload workflow');
  }

  async listFiles(path: string): Promise<StoredFile[]> {
    if (!path.endsWith('/input')) throw new StorageError(400, 'Invalid volume input path');
    const result = await Promise.all(['camera-A.mp4', 'camera-B.mp4'].map(async name => {
      const response = await this.request('HEAD', `${this.publicRoot}/${path}/${name}`);
      if (response.status === 404) return undefined;
      await this.checked(Promise.resolve(response));
      const size = Number(response.headers.get('Content-Length'));
      return { name, size, id: name };
    }));
    return result.filter((file): file is NonNullable<typeof file> => file !== undefined);
  }

  async listJobFolders(cursor?: string) {
    const query: Record<string, string> = {
      'list-type': '2', prefix: this.publicRoot + '/', delimiter: '/', 'max-keys': '25',
    };
    if (cursor) query['continuation-token'] = cursor;
    const xml = await (await this.checked(this.request('GET', '', query))).text();
    const names = (xml.match(/<CommonPrefixes>[\s\S]*?<\/CommonPrefixes>/g) ?? [])
      .map(entry => tag(entry, 'Prefix')?.split('/')[1] ?? '');
    return { names, nextCursor: tag(xml, 'NextContinuationToken') };
  }

  async readJson<T>(path: string): Promise<T> {
    return (await this.checked(this.request('GET', `${this.publicRoot}/${path}`))).json<T>();
  }

  async writeJson(path: string, value: unknown) {
    await (await this.checked(this.request('PUT', `${this.publicRoot}/${path}`, {}, {
      body: JSON.stringify(value), headers: { 'Content-Type': 'application/json' },
    }))).body?.cancel();
  }

  readFile(path: string, range: string | null) {
    return this.request('GET', `${this.publicRoot}/${path}`, {}, {
      headers: range ? { Range: range } : {},
    });
  }

  async beginVideo(path: string) {
    const xml = await (await this.checked(this.request('POST', `${this.publicRoot}/${path}`, {
      uploads: '',
    }, { headers: { 'Content-Type': 'video/mp4' } }))).text();
    const uploadId = tag(xml, 'UploadId');
    if (!uploadId) throw new StorageError(502, 'RunPod did not return an upload ID');
    return uploadId;
  }

  async videoParts(path: string, uploadId: string) {
    const parts: { number: number; size: number; etag: string }[] = [];
    let marker: string | undefined;
    do {
      const query: Record<string, string> = { uploadId };
      if (marker) query['part-number-marker'] = marker;
      const xml = await (await this.checked(this.request('GET', `${this.publicRoot}/${path}`, query))).text();
      for (const block of xml.match(/<Part>[\s\S]*?<\/Part>/g) ?? []) {
        const etag = tag(block, 'ETag');
        if (!etag) throw new StorageError(502, 'RunPod returned an invalid upload part');
        parts.push({ number: Number(tag(block, 'PartNumber')), size: Number(tag(block, 'Size')), etag });
      }
      if (tag(xml, 'IsTruncated') !== 'true') break;
      const next = tag(xml, 'NextPartNumberMarker');
      if (!next || next === marker) throw new StorageError(502, 'RunPod could not list upload parts');
      marker = next;
    } while (marker);
    return parts.sort((a, b) => a.number - b.number);
  }

  async putVideoPart(path: string, uploadId: string, part: number, bytes: ArrayBuffer) {
    // Sign the actual bytes so SigV4 can verify the request payload.
    const hash = await crypto.subtle.digest('SHA-256', bytes);
    const payloadHash = Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('');
    const response = await this.checked(this.request('PUT', `${this.publicRoot}/${path}`, {
      uploadId, partNumber: String(part),
    }, { body: bytes, headers: { 'X-Amz-Content-Sha256': payloadHash } }));
    await response.body?.cancel();
  }

  async finishVideo(path: string, uploadId: string, parts: Awaited<ReturnType<RunpodStorage['videoParts']>>) {
    const body = '<CompleteMultipartUpload>' + parts.map(part =>
      `<Part><PartNumber>${part.number}</PartNumber><ETag>${escapeXml(part.etag)}</ETag></Part>`
    ).join('') + '</CompleteMultipartUpload>';
    const xml = await (await this.checked(this.request('POST', `${this.publicRoot}/${path}`, { uploadId }, {
      body, headers: { 'Content-Type': 'application/xml' },
    }))).text();
    if (!/<(?:\w+:)?CompleteMultipartUploadResult\b/.test(xml))
      throw new StorageError(502, 'RunPod could not complete the video upload');
  }
}
