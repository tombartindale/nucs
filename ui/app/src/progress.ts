// The production progress model: where a topic is up to in the things people do, rather
// than in the pipeline's own steps. Scripted → recorded (filmed) → edited → translated →
// published (composed). Shared by the programme page, the module grid and the topic header
// so they always agree on what each one means.
import type { Lang, TopicMark, TopicStatus } from '@beacon/shared';

export const hasArtifact = (r: TopicStatus, key: string) => r.artifacts.some((a) => a.key === key && a.exists);
/** The edited master is in the system: it has been both filmed and edited. */
export const recordingIn = (r: TopicStatus) => hasArtifact(r, 'edit/master.mp4');

export const scripted = (r: TopicStatus) => r.en.stage !== 'planned';

/** Why a mark counts as done without being ticked, or null if only the tick can say so.
 *  Each mark implies the one before it, and a recording on disk implies both. */
export function markImplied(r: TopicStatus, key: TopicMark): string | null {
  if (recordingIn(r)) return 'Recording received';
  if (key === 'filmed' && r.marks.edited) return 'Marked edited, so it has been filmed';
  return null;
}
export const markDone = (r: TopicStatus, key: TopicMark) => !!r.marks[key] || !!markImplied(r, key);

/** The translated copy is in the system once the stage reaches 'returned'. */
export function translated(r: TopicStatus, lang: Lang = 'zh') {
  const l = r[lang], at = l.stages.indexOf('returned');
  return at >= 0 && l.stage_index >= at;
}

/** Published means composed: a full (non-draft) compose has run and passed. A stale one
 *  still counts, since the video exists; `publishedStale` says it needs re-composing. */
export function published(r: TopicStatus, lang: Lang) {
  const st = r[lang].steps.compose;
  return !!st?.exists && st.ok !== false && !st.draft;
}
export const publishedStale = (r: TopicStatus, lang: Lang) => published(r, lang) && r[lang].steps.compose?.fresh === false;
