// The QA task list's filter, shared with the review screen so "Next" steps through the same
// list the reviewer was looking at. Remembered for the tab (a convenience only).
import { computed, reactive, watch } from 'vue';
import type { QaItem } from '@beacon/shared';
import { useBeacon } from '@/stores/beacon';

export type QaShow = 'outstanding' | 'to_review' | 'changes_needed' | 'approved' | 'all';
interface Filter { show: QaShow; module: string | null; type: 'video' | 'activity' | null }

const KEY = 'qa-filter';
function load(): Filter {
  try {
    const v = JSON.parse(sessionStorage.getItem(KEY) || 'null');
    if (v && typeof v === 'object') return { show: v.show || 'outstanding', module: v.module ?? null, type: v.type ?? null };
  } catch { /* storage unavailable: defaults */ }
  return { show: 'outstanding', module: null, type: null };
}
const filter = reactive<Filter>(load());
watch(filter, (f) => { try { sessionStorage.setItem(KEY, JSON.stringify(f)); } catch { /* ignore */ } });

/** Waiting on a reviewer: never looked at, or changed since someone did. */
export const outstanding = (i: QaItem) => i.status === 'to_review' || i.changedSinceReview;

export function matches(i: QaItem, f: Filter = filter): boolean {
  if (f.module && i.module !== f.module) return false;
  if (f.type && i.type !== f.type) return false;
  if (f.show === 'all') return true;
  if (f.show === 'outstanding') return outstanding(i);
  return i.status === f.show;
}

export function useQaFilter() {
  const beacon = useBeacon();
  const items = computed(() => (beacon.qa || []).filter((i) => matches(i)));
  return { filter, items };
}
