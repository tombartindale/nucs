// Wrapped-line-aware gutter for a textarea editor: each line number gets the pixel height
// its line actually takes (long lines wrap), measured in a hidden copy of the text laid out
// at the same width, with per-line diagnostic severity for coloring. Shared by EditorPage
// (topic.md) and DocEditorPage (module documents) -- the two textarea editors in the app.
import { nextTick, onBeforeUnmount, ref, shallowRef, type Ref, type ShallowRef } from 'vue';
import type { Diagnostic } from '@beacon/shared';

const LINE_PX = 21; // must match .ed-text line-height in app.css

export function useLineGutter(text: Ref<string>, problems: ShallowRef<Diagnostic[]>) {
  const textarea = ref<HTMLTextAreaElement | null>(null);
  const measure = ref<HTMLDivElement | null>(null);
  const gutter = ref<HTMLDivElement | null>(null);
  const gutterLines = shallowRef<Array<{ height: number; level: string }>>([]);
  let lineTops: number[] = [];
  let frame = 0;

  function drawGutter() {
    const ta = textarea.value, m = measure.value;
    if (!ta || !m) return;
    const lines = text.value.split('\n');
    m.style.width = `${ta.clientWidth}px`;
    m.replaceChildren(...lines.map((l) => { const d = document.createElement('div'); d.textContent = l || ' '; return d; }));
    const heights = [...m.children].map((d) => d.getBoundingClientRect().height || LINE_PX);
    lineTops = [];
    let top = 0;
    for (const h of heights) { lineTops.push(top); top += h; }
    const byLine = new Map<number, string>();
    for (const p of problems.value) if (p.line) byLine.set(p.line, byLine.get(p.line) === 'error' || p.level === 'error' ? 'error' : p.level);
    gutterLines.value = heights.map((height, i) => ({ height, level: byLine.get(i + 1) || '' }));
    void nextTick(() => { if (gutter.value) gutter.value.scrollTop = ta.scrollTop; });
  }
  const redrawSoon = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(drawGutter); };
  const resizeObs = new ResizeObserver(redrawSoon);

  function jumpTo(line: number) {
    const ta = textarea.value;
    if (!ta) return;
    const lines = text.value.split('\n');
    const l = Math.max(1, Math.min(line, lines.length));
    let start = 0;
    for (let i = 0; i < l - 1; i++) start += lines[i].length + 1;
    ta.focus();
    ta.setSelectionRange(start, start + lines[l - 1].length);
    ta.scrollTop = Math.max(0, (lineTops[l - 1] ?? (l - 1) * LINE_PX) - 5 * LINE_PX);
  }

  function observe() { if (textarea.value) resizeObs.observe(textarea.value); }
  onBeforeUnmount(() => { cancelAnimationFrame(frame); resizeObs.disconnect(); });

  return { textarea, measure, gutter, gutterLines, drawGutter, redrawSoon, jumpTo, observe };
}
