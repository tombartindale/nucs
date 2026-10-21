// Video lives in S3, not on the programme volume (see tooling/bcn/storage.py). bcn leaves a
// pointer file (<video>.s3.json) where the mp4 would be; this module turns that pointer into
// a short-lived presigned URL for playback, and runs browser uploads straight to the bucket
// as S3 multipart uploads so the API never carries the video bytes.
//
// Browser uploads land under <prefix>/_incoming/<token>/ and are only moved to the topic's
// real key by `bcn media adopt` once checked, so a bad or abandoned upload can never replace
// an existing master.
import { randomUUID } from 'node:crypto';
import { createReadStream, createWriteStream, readFileSync, statSync } from 'node:fs';
import { pipeline } from 'node:stream/promises';
import type { Readable } from 'node:stream';
import {
  AbortMultipartUploadCommand, CompleteMultipartUploadCommand, CreateMultipartUploadCommand, DeleteObjectsCommand, GetObjectCommand,
  PutObjectCommand, S3Client, UploadPartCommand,
} from '@aws-sdk/client-s3';
import { GetFederationTokenCommand, STSClient } from '@aws-sdk/client-sts';
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

/** A runner job's AWS credentials: temporary, and limited to that job's own objects. */
export interface JobCredentials { access_key_id: string; secret_access_key: string; session_token: string; expiration: string }
const RUNNER_CREDENTIAL_SECONDS = 12 * 3600;      // longer than any compose; the job holds them only while it runs

export interface Pointer { key: string; bucket?: string; size: number; version_id?: string | null }

export class MediaStore {
  private readonly s3: S3Client | null;
  readonly bucket: string;
  readonly prefix: string;

  private readonly stsOptions: { region: string; endpoint?: string };

  constructor(env: NodeJS.ProcessEnv = process.env) {
    this.bucket = env.S3_MEDIA_BUCKET || '';
    // An S3-compatible store with its own STS (e.g. a local test server) names it here;
    // otherwise this is AWS STS, called with the server's own (IAM user) credentials.
    this.stsOptions = { region: env.S3_MEDIA_REGION || env.AWS_DEFAULT_REGION || 'us-east-1',
      endpoint: env.S3_MEDIA_STS_ENDPOINT || undefined };
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

  // -- remote runner hand-off (runner-api.ts) ---------------------------------------------------
  // A runner job's input snapshot and output archive pass through <prefix>/_jobs/<id>/. The
  // runner only ever sees presigned URLs for these two objects, never credentials for them.

  jobKey(id: number, name: string): string { return this.join(`_jobs/${id}/${name}`); }

  async putFile(key: string, file: string, contentType = 'application/gzip'): Promise<void> {
    await this.client.send(new PutObjectCommand({
      Bucket: this.bucket, Key: key, Body: createReadStream(file), ContentLength: statSync(file).size, ContentType: contentType,
    }));
  }

  async getFile(key: string, file: string): Promise<void> {
    const res = await this.client.send(new GetObjectCommand({ Bucket: this.bucket, Key: key }));
    await pipeline(res.Body as Readable, createWriteStream(file));
  }

  async presignObject(key: string, method: 'get' | 'put', seconds = UPLOAD_SECONDS): Promise<string> {
    const cmd = method === 'get' ? new GetObjectCommand({ Bucket: this.bucket, Key: key })
      : new PutObjectCommand({ Bucket: this.bucket, Key: key, ContentType: 'application/gzip' });
    return getSignedUrl(this.client, cmd, { expiresIn: seconds });
  }

  /** Credentials a runner's bcn uses for one job's video: read anything under the job's
   *  target, write only its build/ outputs. The server's own credentials never leave it;
   *  these are federated from them (sts:GetFederationToken) with a session policy, so they
   *  can do no more than both that policy and the server's own policy allow, and expire. */
  async jobCredentials(id: number, target: string): Promise<JobCredentials> {
    const scope = this.join(target === '.' ? '' : `${target}/`);
    const bucket = `arn:aws:s3:::${this.bucket}`;
    const policy = {
      Version: '2012-10-17',
      Statement: [
        // Without ListBucket a missing object reads as 403 rather than 404 (deploy/UPGRADE-S3-MEDIA.md).
        { Effect: 'Allow', Action: ['s3:ListBucket'], Resource: bucket, Condition: { StringLike: { 's3:prefix': [`${scope}*`] } } },
        { Effect: 'Allow', Action: ['s3:GetObject', 's3:GetObjectVersion'], Resource: `${bucket}/${scope}*` },
        { Effect: 'Allow', Action: ['s3:PutObject', 's3:AbortMultipartUpload', 's3:ListMultipartUploadParts'],
          Resource: [`${bucket}/${scope}build/*`, `${bucket}/${scope}*/build/*`] },
      ],
    };
    const sts = new STSClient(this.stsOptions);
    const res = await sts.send(new GetFederationTokenCommand({
      Name: `nucs-runner-${id}`.slice(0, 32), Policy: JSON.stringify(policy), DurationSeconds: RUNNER_CREDENTIAL_SECONDS,
    }));
    const c = res.Credentials;
    if (!c?.AccessKeyId || !c.SecretAccessKey || !c.SessionToken) throw new Error('STS returned no credentials.');
    return { access_key_id: c.AccessKeyId, secret_access_key: c.SecretAccessKey, session_token: c.SessionToken,
      expiration: c.Expiration ? new Date(c.Expiration).toISOString() : '' };
  }

  async deleteKeys(keys: string[]): Promise<void> {
    if (!keys.length) return;
    await this.client.send(new DeleteObjectsCommand({ Bucket: this.bucket, Delete: { Objects: keys.map((Key) => ({ Key })), Quiet: true } }));
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
