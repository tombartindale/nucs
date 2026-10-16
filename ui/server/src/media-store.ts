// Video lives in S3, not on the programme volume (see tooling/bcn/storage.py). bcn leaves a
// pointer file (<video>.s3.json) where the mp4 would be; this module turns that pointer into
// a short-lived presigned URL for playback, and runs browser uploads straight to the bucket
// as S3 multipart uploads so the API never carries the video bytes.
//
// Browser uploads land under <prefix>/_incoming/<token>/ and are only moved to the topic's
// real key by `bcn media adopt` once checked, so a bad or abandoned upload can never replace
// an existing master.
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import {
  AbortMultipartUploadCommand, CompleteMultipartUploadCommand, CreateMultipartUploadCommand, S3Client, UploadPartCommand,
} from '@aws-sdk/client-s3';
import { getSignedUrl } from '@aws-sdk/s3-request-presigner';
import { BadRequest } from './errors.js';

export const PART_BYTES = 64 * 1024 * 1024;       // S3 allows 10,000 parts, so this reaches 640 GB
const MAX_PARTS = 10_000;
const PLAY_SECONDS = 3600;
const UPLOAD_SECONDS = 12 * 3600;                 // long enough for a slow connection to finish a large file

export interface UploadPlan {
  key: string;
  upload_id: string;
  part_bytes: number;
  parts: { n: number; url: string }[];
}

export interface Pointer { key: string; bucket?: string; size: number; version_id?: string | null }

export class MediaStore {
  private readonly s3: S3Client | null;
  readonly bucket: string;
  readonly prefix: string;

  constructor(env: NodeJS.ProcessEnv = process.env) {
    this.bucket = env.S3_MEDIA_BUCKET || '';
    this.prefix = (env.S3_MEDIA_PREFIX ?? 'media').replace(/^\/+|\/+$/g, '');
    this.s3 = this.bucket
      ? new S3Client({ region: env.S3_MEDIA_REGION || env.AWS_DEFAULT_REGION || 'us-east-1', endpoint: env.S3_MEDIA_ENDPOINT || undefined,
          forcePathStyle: Boolean(env.S3_MEDIA_ENDPOINT) })
      : null;
  }

  get enabled(): boolean { return this.s3 !== null; }

  private get client(): S3Client {
    if (!this.s3) throw new BadRequest('Object storage is not configured.');
    return this.s3;
  }

  private join(rest: string): string { return this.prefix ? `${this.prefix}/${rest}` : rest; }

  /** The staging area browser uploads go to. */
  get incomingRoot(): string { return this.join('_incoming/'); }

  /** Only a key this server handed out for an upload may be completed or adopted. */
  checkIncoming(key: string): void {
    if (!key.startsWith(this.incomingRoot) || key.includes('..') || key.length > 300) throw new BadRequest('Not an upload key.');
  }

  /** Reads a bcn pointer file; null if there is none (the video is a plain local file, or absent). */
  static readPointer(pointerFile: string): Pointer | null {
    try {
      const p = JSON.parse(readFileSync(pointerFile, 'utf8')) as Pointer;
      return typeof p.key === 'string' && p.key ? p : null;
    } catch { return null; }
  }

  async presignGet(key: string, opts: { filename?: string; versionId?: string | null } = {}): Promise<string> {
    const { GetObjectCommand } = await import('@aws-sdk/client-s3');
    return getSignedUrl(this.client, new GetObjectCommand({
      Bucket: this.bucket, Key: key, ResponseContentType: 'video/mp4',
      ...(opts.filename ? { ResponseContentDisposition: `attachment; filename="${opts.filename.replace(/[\r\n"]/g, '')}"` } : {}),
    }), { expiresIn: PLAY_SECONDS });
  }

  async startUpload(name: string, bytes: number): Promise<UploadPlan> {
    if (!Number.isFinite(bytes) || bytes <= 0) throw new BadRequest('The upload is empty.');
    const nParts = Math.ceil(bytes / PART_BYTES);
    if (nParts > MAX_PARTS) throw new BadRequest('That file is too large to upload.');
    const key = `${this.incomingRoot}${randomUUID()}/${name}`;
    const created = await this.client.send(new CreateMultipartUploadCommand({ Bucket: this.bucket, Key: key, ContentType: 'video/mp4' }));
    if (!created.UploadId) throw new BadRequest('Could not start the upload.');
    const parts = await Promise.all(Array.from({ length: nParts }, async (_, i) => ({
      n: i + 1,
      url: await getSignedUrl(this.client, new UploadPartCommand({ Bucket: this.bucket, Key: key, UploadId: created.UploadId, PartNumber: i + 1 }),
        { expiresIn: UPLOAD_SECONDS }),
    })));
    return { key, upload_id: created.UploadId, part_bytes: PART_BYTES, parts };
  }

  async completeUpload(key: string, uploadId: string, parts: { n: number; etag: string }[]): Promise<void> {
    this.checkIncoming(key);
    if (!parts.length) throw new BadRequest('No parts were uploaded.');
    await this.client.send(new CompleteMultipartUploadCommand({
      Bucket: this.bucket, Key: key, UploadId: uploadId,
      MultipartUpload: { Parts: [...parts].sort((a, b) => a.n - b.n).map((p) => ({ PartNumber: p.n, ETag: p.etag })) },
    }));
  }

  async abortUpload(key: string, uploadId: string): Promise<void> {
    this.checkIncoming(key);
    await this.client.send(new AbortMultipartUploadCommand({ Bucket: this.bucket, Key: key, UploadId: uploadId }));
  }
}
