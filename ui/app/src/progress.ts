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

/** The English subtitles are ready to hand to the translator: bcn says sending them is
 *  Mandarin's next step (through bcn subtitles, every mishearing reviewed), or they have
 *  gone. The same rule as the topic page's "Ready for translation" tile and the delivery
 *  planner (ui/server/src/planning.ts), which schedules against it. */
export function readyToTranslate(r: TopicStatus) {
  const at = r.zh.stages.indexOf('out_for_translation');
  return r.zh.next === 'translation_export' || (at >= 0 && r.zh.stage_index >= at);
}
/** The editor's English subtitles are in the system (they can arrive before the video). */
export const subtitlesIn = (r: TopicStatus) => hasArtifact(r, 'edit/master.srt');

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

/** The topic's own intro/outro (bcn bumpers) for one language. bcn lists a row for every
 *  bumper the programme needs (one set per frame size in [compose].default_layouts), so a
 *  row that doesn't exist yet counts as out of date: compose has no topic bumper for that
 *  size and falls back to [bumpers], or none. Shared by the module grid and the topic page
 *  so they always agree. */
export function bumperStatus(r: Pick<TopicStatus, 'artifacts'>, lang: Lang): 'ok' | 'stale' | 'none' {
  const arts = r.artifacts.filter((a) => a.kind.startsWith('bumper') && a.lang === lang);
  if (!arts.some((a) => a.exists)) return 'none';
  return arts.some((a) => !a.exists || a.stale) ? 'stale' : 'ok';
}
