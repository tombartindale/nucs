// Mandarin QA: which deliverables are ready for an internal review, and where each stands.
//
// Pure: bcn status in, plus the review rows from the UI database, QA items out. Nothing
// here decides pipeline state. An item is "ready for QA" simply because its Mandarin file
// exists: a topic's composed Mandarin video, or a unit's Mandarin activity.
import type { QaComment, QaCommentKind, QaItem, QaStatus, StatusEnvelope } from '@beacon/shared';
import type { QaCommentRow, QaReviewRow } from './db.js';

export const QA_STATUSES: readonly QaStatus[] = ['to_review', 'changes_needed', 'approved'];
export const QA_KINDS: readonly QaCommentKind[] = ['comment', 'suggestion'];
export const QA_ITEM = /^(video:[A-Z]{2}\d{4}-U\d{2}-T\d{2}|activity:[A-Z]{2}\d{4}\/U\d{2})$/;
export const QA_COMMENT_MAX = 5_000;

const ACTIVITY_ZH = /^([A-Z]{2}\d{4})\/(U\d{2})\/activity\.zh\.md$/;
const VIDEO_KEY = 'build/composed.zh.mp4';

/** The files QA needs, before any review state is merged in. */
type Candidate = Omit<QaItem, 'status' | 'statusBy' | 'statusAt' | 'changedSinceReview' | 'openComments' | 'comments' | 'lastActivity'>;

function candidates(env: StatusEnvelope, docMtime: (path: string) => string | null): Candidate[] {
  const out: Candidate[] = [];
  for (const t of env.results) {
    const video = t.artifacts.find((a) => a.key === VIDEO_KEY);
    if (!video?.exists) continue;
    const srt = t.artifacts.find((a) => a.kind === 'composed_subtitles' && a.lang === 'zh' && a.exists);
    out.push({
      item: `video:${t.topic}`, type: 'video', module: t.module, unit: t.unit, topic: t.topic, title: t.title,
      path: video.path, srt: srt?.path ?? null,
      mtime: video.mtime, stale: video.stale === true,
    });
  }
  for (const [module, summary] of Object.entries(env.summary.modules)) {
    for (const d of summary.documents) {
      const m = ACTIVITY_ZH.exec(d.path);
      if (!m || !d.exists || m[1] !== module) continue;
      const unit = m[2];
      out.push({
        item: `activity:${module}/${unit}`, type: 'activity', module, unit, topic: null,
        title: summary.unit_titles[unit] || unit, path: d.path, srt: null,
        mtime: docMtime(d.path), stale: false,
      });
    }
  }
  return out;
}

/** Every item ready for QA, with its verdict and comment counts, in programme order. */
export function buildQaItems(env: StatusEnvelope, reviews: Map<string, QaReviewRow>,
  docMtime: (path: string) => string | null): QaItem[] {
  const items = candidates(env, docMtime).map((c): QaItem => {
    const r = reviews.get(c.item);
    const status = (r && (QA_STATUSES as string[]).includes(r.status) ? r.status : 'to_review') as QaStatus;
    const changed = status !== 'to_review' && !!c.mtime && !!r?.reviewed_mtime && c.mtime > r.reviewed_mtime;
    const last = [r?.status_at ?? null, r?.last_comment ?? null].filter((x): x is string => !!x).sort().pop() ?? null;
    return {
      ...c, status, statusBy: r?.status_by ?? '', statusAt: r?.status_at ?? null,
      changedSinceReview: changed, openComments: r?.open_comments ?? 0, comments: r?.comments ?? 0, lastActivity: last,
    };
  });
  // Videos and the unit's activity side by side: by module, then unit, then topic (activity last).
  const key = (i: QaItem) => `${i.module}/${i.unit}/${i.topic ?? '~'}`;
  return items.sort((a, b) => (key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0));
}

export function toQaComment(r: QaCommentRow, me: string | null): QaComment {
  return {
    id: r.id, item: r.item, body: r.body, kind: (r.kind === 'suggestion' ? 'suggestion' : 'comment'),
    anchorT: r.anchor_t, anchorLine: r.anchor_line, by: r.by, at: r.at,
    resolved: r.resolved, resolvedBy: r.resolved_by, resolvedAt: r.resolved_at,
    mine: !!me && r.by.toLowerCase() === me.toLowerCase(),
  };
}
