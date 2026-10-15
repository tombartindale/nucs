// MediaStore needs no database: presigning is local arithmetic, and the one network call
// (CreateMultipartUpload) is answered by a stub standing in for S3.
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { createServer, type Server } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { validateArgs } from '../src/jobs.js';
import { MediaStore, PART_BYTES } from '../src/media-store.js';

let stub: Server;
let store: MediaStore;
const seen: string[] = [];

beforeAll(async () => {
  // The SDK's credential chain reads the real environment, whatever env the store was given.
  Object.assign(process.env, { AWS_ACCESS_KEY_ID: 'test', AWS_SECRET_ACCESS_KEY: 'test', AWS_DEFAULT_REGION: 'us-east-1' });
  stub = createServer((req, res) => {
    seen.push(`${req.method} ${req.url}`);
    res.setHeader('Content-Type', 'application/xml');
    res.end('<?xml version="1.0"?><InitiateMultipartUploadResult><Bucket>b</Bucket><Key>k</Key><UploadId>upload-1</UploadId></InitiateMultipartUploadResult>');
  });
  await new Promise<void>((r) => stub.listen(0, '127.0.0.1', r));
  const port = (stub.address() as { port: number }).port;
  store = new MediaStore({ S3_MEDIA_BUCKET: 'media-bucket', S3_MEDIA_PREFIX: 'media', S3_MEDIA_ENDPOINT: `http://127.0.0.1:${port}`,
    AWS_ACCESS_KEY_ID: 'test', AWS_SECRET_ACCESS_KEY: 'test', AWS_DEFAULT_REGION: 'us-east-1' });
});
afterAll(() => { stub.close(); });

describe('MediaStore', () => {
  it('is disabled without a bucket', () => {
    expect(new MediaStore({}).enabled).toBe(false);
    expect(store.enabled).toBe(true);
  });

  it('presigns a playback URL for the pointer key', async () => {
    const url = new URL(await store.presignGet('media/KV7015/U01/T01/edit/master.mp4'));
    expect(url.pathname).toContain('media-bucket/media/KV7015/U01/T01/edit/master.mp4');
    expect(url.searchParams.get('X-Amz-Signature')).toBeTruthy();
    expect(url.searchParams.get('X-Amz-Expires')).toBe('3600');
    expect(url.searchParams.get('response-content-type')).toBe('video/mp4');
  });

  it('plans a multipart upload into staging, one URL per part', async () => {
    const plan = await store.startUpload('master.mp4', PART_BYTES * 2 + 1);
    expect(plan.upload_id).toBe('upload-1');
    expect(plan.key.startsWith('media/_incoming/')).toBe(true);
    expect(plan.key.endsWith('/master.mp4')).toBe(true);
    expect(plan.parts.map((p) => p.n)).toEqual([1, 2, 3]);
    for (const p of plan.parts) {
      const u = new URL(p.url);
      expect(u.searchParams.get('partNumber')).toBe(String(p.n));
      expect(u.searchParams.get('uploadId')).toBe('upload-1');
    }
    expect(seen.some((s) => s.startsWith('POST') && s.includes('uploads'))).toBe(true);
  });

  it('refuses empty and oversized uploads', async () => {
    await expect(store.startUpload('master.mp4', 0)).rejects.toThrow();
    await expect(store.startUpload('master.mp4', PART_BYTES * 10_001)).rejects.toThrow();
  });

  it('only completes or aborts keys it issued', async () => {
    for (const bad of ['media/KV7015/U01/T01/edit/master.mp4', 'media/_incoming/../x', 'other/_incoming/x/master.mp4', '']) {
      await expect(store.completeUpload(bad, 'u', [{ n: 1, etag: 'e' }])).rejects.toThrow();
      await expect(store.abortUpload(bad, 'u')).rejects.toThrow();
    }
  });

  it('reads bcn pointer files', () => {
    const dir = mkdtempSync(join(tmpdir(), 'ptr-'));
    try {
      writeFileSync(join(dir, 'ok.json'), JSON.stringify({ key: 'media/a/master.mp4', size: 5 }));
      writeFileSync(join(dir, 'bad.json'), '{"nokey":1}');
      writeFileSync(join(dir, 'junk.json'), 'not json');
      expect(MediaStore.readPointer(join(dir, 'ok.json'))?.key).toBe('media/a/master.mp4');
      expect(MediaStore.readPointer(join(dir, 'bad.json'))).toBeNull();
      expect(MediaStore.readPointer(join(dir, 'junk.json'))).toBeNull();
      expect(MediaStore.readPointer(join(dir, 'missing.json'))).toBeNull();
    } finally { rmSync(dir, { recursive: true, force: true }); }
  });
});

describe('media job arguments', () => {
  it('allows the adopt hand-off only', () => {
    expect(() => validateArgs('media', ['KV7015/U01/T01'], { action: 'adopt', upload_key: 'media/_incoming/abc-123/master.mp4', replace: true })).not.toThrow();
    expect(() => validateArgs('media', ['KV7015/U01/T01'], { action: 'migrate' })).toThrow();
    expect(() => validateArgs('media', ['KV7015/U01/T01'], { action: 'gc' })).toThrow();
    expect(() => validateArgs('media', ['KV7015/U01/T01'], { action: 'adopt', upload_key: 'media/_incoming/../x' })).toThrow();
    expect(() => validateArgs('media', ['KV7015/U01/T01'], { action: 'adopt', upload_key: 'a b' })).toThrow();
    expect(() => validateArgs('media', ['KV7015/U01/T01'], { apply: true })).toThrow();
  });
});
