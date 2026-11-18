// Pure: a synthetic bcn status plus review rows into buildQaItems. No DB, no bcn.
import { describe, expect, it } from 'vitest';
import type { StatusArtifact, StatusEnvelope, TopicStatus } from '@beacon/shared';
import type { QaCommentRow, QaReviewRow } from '../src/db.js';
import { buildQaItems, QA_ITEM, toQaComment } from '../src/qa.js';

function art(key: string, kind: string, lang: 'en' | 'zh', exists: boolean, extra: Partial<StatusArtifact> = {}): StatusArtifact {
  return { key, path: `KV7015/U01/T01/${key}`, lang, kind, exists, bytes: 1, mtime: '2026-11-01T10:00:00Z', hydration: 'local', stale: false, ...extra };
}

function topic(id: string, artifacts: StatusArtifact[]): TopicStatus {
  const [module, unit] = id.split('-');
  return { topic: id, module, unit, title: `Title ${id}`, path: id.split('-').join('/'), artifacts } as unknown as TopicStatus;
}

function env(results: TopicStatus[], documents: Array<{ path: string; exists: boolean }> = []): StatusEnvelope {
  return {
    results,
    summary: { modules: { KV7015: { documents, unit_titles: { U01: 'Questions', U02: 'Answers' } } } },
  } as unknown as StatusEnvelope;
}

const VIDEO = (exists = true, extra: Partial<StatusArtifact> = {}) => art('build/composed.zh.mp4', 'composed', 'zh', exists, extra);
const SRT = art('build/subtitles/KV7015-U01-T01.zh.delivery.srt', 'composed_subtitles', 'zh', true);
const noMtime = () => null;

function review(item: string, extra: Partial<QaReviewRow> = {}): Map<string, QaReviewRow> {
  return new Map([[item, {
    item, status: 'to_review', status_by: '', status_at: null, reviewed_mtime: null,
    comments: 0, open_comments: 0, last_comment: null, ...extra,
  }]]);
}

describe('buildQaItems', () => {
  it('lists a topic once its composed Mandarin video exists, with its Mandarin subtitles', () => {
    const items = buildQaItems(env([
      topic('KV7015-U01-T01', [VIDEO(), SRT, art('build/composed.mp4', 'composed', 'en', true)]),
      topic('KV7015-U01-T02', [VIDEO(false)]),
    ]), new Map(), noMtime);
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      item: 'video:KV7015-U01-T01', type: 'video', topic: 'KV7015-U01-T01', unit: 'U01', title: 'Title KV7015-U01-T01',
      path: 'KV7015/U01/T01/build/composed.zh.mp4', srt: 'KV7015/U01/T01/build/subtitles/KV7015-U01-T01.zh.delivery.srt',
      status: 'to_review', comments: 0, changedSinceReview: false,
    });
  });

  it('ignores the English composed video and alternative layouts', () => {
    const items = buildQaItems(env([topic('KV7015-U01-T01', [
      art('build/composed.mp4', 'composed', 'en', true), art('build/composed.zh.portrait.mp4', 'composed', 'zh', true),
    ])]), new Map(), noMtime);
    expect(items).toEqual([]);
  });

  it('lists each unit whose Mandarin activity exists, titled by the unit', () => {
    const items = buildQaItems(env([], [
      { path: 'KV7015/U01/activity.md', exists: true },
      { path: 'KV7015/U01/activity.zh.md', exists: true },
      { path: 'KV7015/U02/activity.zh.md', exists: false },
      { path: 'KV7015/assignment-1.zh.md', exists: true },
    ]), new Map(), (p) => (p === 'KV7015/U01/activity.zh.md' ? '2026-11-02T00:00:00.000Z' : null));
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({
      item: 'activity:KV7015/U01', type: 'activity', topic: null, title: 'Questions',
      path: 'KV7015/U01/activity.zh.md', mtime: '2026-11-02T00:00:00.000Z',
    });
    expect(QA_ITEM.test(items[0].item)).toBe(true);
  });

  it('orders by unit, topics before the unit activity', () => {
    const items = buildQaItems(env([topic('KV7015-U01-T02', [VIDEO()]), topic('KV7015-U01-T01', [VIDEO()])],
      [{ path: 'KV7015/U01/activity.zh.md', exists: true }]), new Map(), noMtime);
    expect(items.map((i) => i.item)).toEqual(['video:KV7015-U01-T01', 'video:KV7015-U01-T02', 'activity:KV7015/U01']);
  });

  it('merges the verdict and comment counts, and the latest activity', () => {
    const items = buildQaItems(env([topic('KV7015-U01-T01', [VIDEO()])]), review('video:KV7015-U01-T01', {
      status: 'approved', status_by: 'a@x.org', status_at: '2026-11-03T00:00:00Z', reviewed_mtime: '2026-11-01T10:00:00Z',
      comments: 3, open_comments: 1, last_comment: '2026-11-04T00:00:00Z',
    }), noMtime);
    expect(items[0]).toMatchObject({
      status: 'approved', statusBy: 'a@x.org', comments: 3, openComments: 1,
      lastActivity: '2026-11-04T00:00:00Z', changedSinceReview: false,
    });
  });

  it('flags a file that changed after its verdict, but not one still to review', () => {
    const later = VIDEO(true, { mtime: '2026-11-05T00:00:00Z' });
    const approved = buildQaItems(env([topic('KV7015-U01-T01', [later])]),
      review('video:KV7015-U01-T01', { status: 'approved', reviewed_mtime: '2026-11-01T10:00:00Z' }), noMtime);
    expect(approved[0].changedSinceReview).toBe(true);
    const pending = buildQaItems(env([topic('KV7015-U01-T01', [later])]),
      review('video:KV7015-U01-T01', { status: 'to_review', reviewed_mtime: '2026-11-01T10:00:00Z' }), noMtime);
    expect(pending[0].changedSinceReview).toBe(false);
  });

  it('treats an unknown stored status as to_review', () => {
    const items = buildQaItems(env([topic('KV7015-U01-T01', [VIDEO()])]), review('video:KV7015-U01-T01', { status: 'bogus' }), noMtime);
    expect(items[0].status).toBe('to_review');
  });
});

describe('toQaComment', () => {
  const row: QaCommentRow = {
    id: 1, item: 'video:KV7015-U01-T01', body: 'Tone is off', kind: 'suggestion', anchor_t: 12.5, anchor_line: null,
    by: 'Reviewer@x.org', at: '2026-11-01T00:00:00Z', resolved: false, resolved_by: null, resolved_at: null,
  };
  it('marks the signed-in author\'s own comments, ignoring case', () => {
    expect(toQaComment(row, 'reviewer@x.org')).toMatchObject({ mine: true, kind: 'suggestion', anchorT: 12.5 });
    expect(toQaComment(row, 'other@x.org').mine).toBe(false);
    expect(toQaComment(row, null).mine).toBe(false);
  });
});

describe('QA_ITEM', () => {
  it('accepts only video and activity keys', () => {
    expect(QA_ITEM.test('video:KV7015-U01-T01')).toBe(true);
    expect(QA_ITEM.test('activity:KV7015/U01')).toBe(true);
    expect(QA_ITEM.test('activity:KV7015/U01/../x')).toBe(false);
    expect(QA_ITEM.test('video:../etc')).toBe(false);
  });
});
