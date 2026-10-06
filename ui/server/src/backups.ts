// Backups kept in S3: a whole-programme bcn export (full/) and a Postgres dump (db/), both
// written nightly by the backup container (deploy/backup/backup.sh). This module only lists,
// streams and restores them for the admin page; it never deletes anything.
import { createWriteStream } from 'node:fs';
import type { Readable } from 'node:stream';
import { GetObjectCommand, ListObjectsV2Command, S3Client } from '@aws-sdk/client-s3';
import { BadRequest } from './errors.js';

export type BackupKind = 'full' | 'db';

export interface BackupItem {
  key: string;        // full S3 key, the handle the admin page passes back
  name: string;       // file name, e.g. 2026-10-06T03-17-00Z.zip
  kind: BackupKind;
  bytes: number;
  modified: string;   // ISO time
}

const KIND_FOLDER: Record<BackupKind, string> = { full: 'full', db: 'db' };

export class Backups {
  private readonly s3: S3Client | null;
  private readonly bucket: string;
  readonly prefix: string;

  constructor(env: NodeJS.ProcessEnv = process.env) {
    this.bucket = env.S3_BACKUP_BUCKET || '';
    this.prefix = (env.S3_BACKUP_PREFIX || 'postgres').replace(/\/+$/, '');
    this.s3 = this.bucket ? new S3Client({ region: env.AWS_DEFAULT_REGION || 'us-east-1' }) : null;
  }

  get enabled(): boolean { return this.s3 !== null; }

  async list(): Promise<BackupItem[]> {
    if (!this.s3) return [];
    const items: BackupItem[] = [];
    for (const kind of Object.keys(KIND_FOLDER) as BackupKind[]) {
      let token: string | undefined;
      do {
        const res = await this.s3.send(new ListObjectsV2Command({
          Bucket: this.bucket, Prefix: `${this.prefix}/${KIND_FOLDER[kind]}/`, ContinuationToken: token,
        }));
        for (const o of res.Contents ?? []) {
          if (!o.Key || o.Key.endsWith('/')) continue;
          items.push({ key: o.Key, name: o.Key.split('/').pop() || o.Key, kind, bytes: Number(o.Size ?? 0),
            modified: (o.LastModified ?? new Date(0)).toISOString() });
        }
        token = res.IsTruncated ? res.NextContinuationToken : undefined;
      } while (token);
    }
    return items.sort((a, b) => (a.modified < b.modified ? 1 : -1));
  }

  /** Only keys the listing itself would show are accepted, so a request cannot read other objects in the bucket. */
  private checkKey(key: string): void {
    const ok = Object.values(KIND_FOLDER).some((f) => key.startsWith(`${this.prefix}/${f}/`))
      && !key.includes('..') && key.length > `${this.prefix}/x/`.length;
    if (!ok) throw new BadRequest('Not a backup.');
  }

  async open(key: string): Promise<{ body: Readable; bytes: number }> {
    if (!this.s3) throw new BadRequest('Backups are not configured (S3_BACKUP_BUCKET is not set).');
    this.checkKey(key);
    const res = await this.s3.send(new GetObjectCommand({ Bucket: this.bucket, Key: key }));
    return { body: res.Body as Readable, bytes: Number(res.ContentLength ?? 0) };
  }

  /** Copies a backup to a local file (used to stage a full backup for restore). */
  async download(key: string, dest: string): Promise<void> {
    const { body } = await this.open(key);
    await new Promise<void>((resolve, reject) => {
      const out = createWriteStream(dest);
      body.pipe(out);
      out.on('finish', resolve);
      out.on('error', reject);
      body.on('error', reject);
    });
  }
}
