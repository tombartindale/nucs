// Uploading one of a topic's own files from the browser: the editor's video and subtitles, or
// the translated subtitles and slides. The server names the file for the topic, so whatever the
// file was called on the sender's disk doesn't matter. Shared by the topic page's upload row and
// the Transfer page's per-topic grid.
import type { JobSummary } from '@beacon/shared';
import { api, ApiError } from '@/api';

export type UploadKind = 'master' | 'en' | 'zh' | 'zh_md';

export interface UploadKindSpec { kind: UploadKind; label: string; short: string; accept: string; path: string; key: string; icon: string }

/** In the order the files usually arrive. `key` is the topic's status artifact for that file. */
export const UPLOAD_KINDS: UploadKindSpec[] = [
  { kind: 'master', label: 'Video', short: 'Edit video', accept: '.mp4,video/mp4', path: 'edit/master.mp4', key: 'edit/master.mp4', icon: 'videocam' },
  { kind: 'en', label: 'English subtitles', short: 'Edit subtitles', accept: '.srt', path: 'edit/master.srt', key: 'edit/master.srt', icon: 'subtitles' },
  { kind: 'zh', label: 'Mandarin subtitles', short: 'Translated subtitles', accept: '.srt', path: 'edit/master.zh.srt', key: 'edit/master.zh.srt', icon: 'subtitles' },
  { kind: 'zh_md', label: 'Mandarin slides', short: 'Translated slides', accept: '.md,text/markdown', path: 'topic.zh.md', key: 'topic.zh.md', icon: 'slideshow' },
];

/** Something an in-flight upload can be cancelled through. */
export interface Abortable { abort(): void }

// fetch() cannot report upload progress, so the file is sent with XMLHttpRequest, which can.
function sendFile<T>(url: string, file: File, onAbortable: (a: Abortable) => void, onProgress: (fraction: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    onAbortable(xhr);
    xhr.open('POST', url);
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      if (xhr.status === 401) { window.location.href = '/login.html'; return; }
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText) as T); } catch { reject(new Error('The server sent back something unexpected.')); }
        return;
      }
      let message = `${xhr.status} ${xhr.statusText}`;
      try { message = JSON.parse(xhr.responseText).error || message; } catch { /* not JSON: keep the status text */ }
      reject(new Error(message));
    };
    xhr.onerror = () => reject(new Error('The upload failed: the connection was lost.'));
    xhr.onabort = () => reject(new Error('Upload cancelled.'));
    xhr.send(file);
  });
}

// Video goes straight to S3 in parts (the server hands out presigned URLs and never sees the
// bytes), then the server is told to check it and put it in place. Returns null when the server
// has no object storage (501), so the caller falls back to the streaming route.
interface UploadPlan { key: string; upload_id: string; part_bytes: number; parts: Array<{ n: number; url: string }> }
const PART_RETRIES = 4;
const PART_CONCURRENCY = 3;

function putPart(url: string, blob: Blob, active: Set<XMLHttpRequest>, onBytes: (loaded: number) => void): Promise<string> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    active.add(xhr);
    xhr.open('PUT', url);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onBytes(e.loaded); };
    xhr.onload = () => {
      active.delete(xhr);
      const etag = xhr.getResponseHeader('ETag');
      if (xhr.status >= 200 && xhr.status < 300 && etag) { resolve(etag); return; }
      reject(new Error(xhr.status >= 200 && xhr.status < 300
        ? 'The storage bucket did not expose the upload receipt (check its CORS settings).' : `Storage refused the upload (${xhr.status}).`));
    };
    xhr.onerror = () => { active.delete(xhr); reject(new Error('The upload failed: the connection was lost.')); };
    xhr.onabort = () => { active.delete(xhr); reject(new Error('Upload cancelled.')); };
    xhr.send(blob);
  });
}

async function uploadDirect(id: string, file: File, replace: boolean, onAbortable: (a: Abortable) => void,
  onProgress: (fraction: number) => void): Promise<JobSummary | null> {
  let plan: UploadPlan;
  try {
    plan = await api<UploadPlan>(`/api/topic/${id}/media/init`, { body: { bytes: file.size } });
  } catch (e) {
    if (e instanceof ApiError && e.status === 501) return null;
    throw e;
  }
  const active = new Set<XMLHttpRequest>();
  let cancelled = false;
  onAbortable({ abort() { cancelled = true; for (const x of active) x.abort(); } });
  const done = new Array<number>(plan.parts.length).fill(0);   // bytes confirmed or in flight, per part
  const etags: Array<{ n: number; etag: string }> = [];
  const report = () => onProgress(done.reduce((a, b) => a + b, 0) / file.size);
  const queue = [...plan.parts];
  const worker = async () => {
    for (let part = queue.shift(); part && !cancelled; part = queue.shift()) {
      const blob = file.slice((part.n - 1) * plan.part_bytes, part.n * plan.part_bytes);
      for (let attempt = 1; ; attempt++) {
        try {
          const etag = await putPart(part.url, blob, active, (loaded) => { done[part.n - 1] = loaded; report(); });
          done[part.n - 1] = blob.size;
          report();
          etags.push({ n: part.n, etag });
          break;
        } catch (e) {
          done[part.n - 1] = 0;
          if (cancelled || attempt >= PART_RETRIES) throw e;
          await new Promise((r) => setTimeout(r, 1000 * attempt));
        }
      }
    }
  };
  try {
    await Promise.all(Array.from({ length: Math.min(PART_CONCURRENCY, plan.parts.length) }, worker));
    if (cancelled) throw new Error('Upload cancelled.');
    return await api<JobSummary>(`/api/topic/${id}/media/complete`, {
      body: { key: plan.key, upload_id: plan.upload_id, parts: etags, replace },
    });
  } catch (e) {
    for (const x of active) x.abort();
    // Free the parts already stored, or the bucket keeps (and bills) them until its lifecycle rule runs.
    await api(`/api/topic/${id}/media/abort`, { body: { key: plan.key, upload_id: plan.upload_id } }).catch(() => undefined);
    throw e;
  }
}

/** Send one file for a topic and return the job that places it. Progress runs 0..1 while bytes
 *  are moving; once this resolves, the server is placing the file (await the job for that). */
export async function uploadTopicFile(id: string, kind: UploadKind, file: File, replace: boolean,
  onAbortable: (a: Abortable) => void, onProgress: (fraction: number) => void): Promise<JobSummary> {
  const job = kind === 'master' ? await uploadDirect(id, file, replace, onAbortable, onProgress) : null;
  return job ?? sendFile<JobSummary>(`/api/topic/${id}/media?kind=${kind}${replace ? '&replace=1' : ''}`, file, onAbortable, onProgress);
}
