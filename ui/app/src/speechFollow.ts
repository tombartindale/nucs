// Voice follow for the teleprompter: on-device speech recognition, and a matcher that finds
// where in the script the last few heard words are.
//
// Recognition is the browser's Web Speech API with processLocally set, so audio never leaves
// the machine. Chrome and Edge have it on the desktop; other browsers get a clear refusal
// rather than a quiet fallback to a cloud service.

// --- matching ---------------------------------------------------------------------------------

const NUMBERS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten',
  'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen', 'twenty'];

// One written token can be several spoken words ("e-learning", "and/or"); the recogniser may
// write numbers either way, so small ones become words on both sides.
export function normalise(token: string): string[] {
  return token.toLowerCase().replace(/[’‘]/g, "'").split(/[\s\-–—/]+/)
    .map((w) => w.replace(/[^\p{L}\p{N}']/gu, '').replace(/'/g, ''))
    .filter(Boolean)
    .map((w) => (/^\d+$/.test(w) && +w <= 20 ? NUMBERS[+w] : w));
}

function close(a: string, b: string) {
  if (a.length !== b.length || a.length < 5) return false;
  let diff = 0;
  for (let i = 0; i < a.length && diff < 2; i++) if (a[i] !== b[i]) diff++;
  return diff < 2;
}
function same(a: string, b: string) {
  if (a === b) return true;
  if (Math.min(a.length, b.length) >= 4 && (a.startsWith(b) || b.startsWith(a))) return true;
  return close(a, b);
}
// "the", "of", "a" turn up everywhere, so they count for half.
const weight = (w: string) => (w.length >= 4 ? 1 : 0.5);

// Weighted longest common subsequence of heard against words[from, to).
function lcs(heard: string[], words: string[], from: number, to: number) {
  const m = to - from;
  let prev = new Array(m + 1).fill(0);
  for (let i = 0; i < heard.length; i++) {
    const row = new Array(m + 1).fill(0);
    for (let j = 0; j < m; j++) {
      const w = words[from + j];
      row[j + 1] = same(heard[i], w) ? prev[j] + weight(w) : Math.max(prev[j + 1], row[j]);
    }
    prev = row;
  }
  return prev[m];
}

// The index of the script word the speaker has just said, or null if the heard words do not
// match anywhere near the cursor well enough to move. Forward moves are cheap; going back
// (a restarted sentence) or jumping far ahead (a skipped paragraph) needs a stronger match.
export function locate(heard: string[], words: string[], cursor: number): number | null {
  const k = heard.length;
  if (k < 2) return null;
  const lo = Math.max(0, cursor - 40);
  const hi = Math.min(words.length, cursor + 150);
  let best: number | null = null;
  let bestScore = -Infinity;
  for (let end = lo; end < hi; end++) {
    // The match must finish on one of the last two heard words; an ad-lib can end a phrase.
    if (!same(heard[k - 1], words[end]) && !same(heard[k - 2], words[end])) continue;
    const score = lcs(heard, words, Math.max(0, end - k - 3), end + 1);
    const jump = end < cursor - 3 || end > cursor + 40;
    if (score < (jump ? 3.5 : 2.5)) continue;
    const dist = end >= cursor ? end - cursor : (cursor - end) * 3;
    const s = score - dist * 0.004;
    if (s > bestScore) { bestScore = s; best = end; }
  }
  return best;
}

// --- recognition ------------------------------------------------------------------------------

interface Alternative { transcript: string }
interface Result { 0: Alternative; isFinal: boolean }
interface ResultEvent { resultIndex: number; results: { length: number; [i: number]: Result } }
interface Recognition {
  lang: string; continuous: boolean; interimResults: boolean; processLocally: boolean;
  onresult: ((e: ResultEvent) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void; stop(): void; abort(): void;
}
type Availability = 'unavailable' | 'downloadable' | 'downloading' | 'available';
interface RecognitionCtor {
  new (): Recognition;
  available?(o: { langs: string[]; processLocally: boolean }): Promise<Availability>;
  install?(o: { langs: string[]; processLocally: boolean }): Promise<boolean>;
}

const HEARD = 8;   // how many recent words the matcher looks at

export interface Listener { stop(): void; forget(): void; lang: string }

// Starts listening and calls onWords with the last few words heard, interim ones included,
// since waiting for final results would put the prompter a sentence behind. Rejects with a
// message fit to show the speaker if on-device recognition is not available.
export async function listen(onWords: (w: string[]) => void, onStatus: (s: string, stopped?: boolean) => void): Promise<Listener> {
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  const Ctor = w.SpeechRecognition || w.webkitSpeechRecognition;
  if (!Ctor?.available) throw new Error('Voice follow needs Chrome or Edge on a desktop, which can recognise speech on this computer.');

  let lang = '';
  let status: Availability = 'unavailable';
  for (const l of new Set([navigator.language, 'en-GB', 'en-US'])) {
    status = await Ctor.available({ langs: [l], processLocally: true });
    if (status !== 'unavailable') { lang = l; break; }
  }
  if (!lang) throw new Error('This browser has no on-device English speech model.');
  if (status !== 'available') {
    onStatus(`downloading the ${lang} speech model…`);
    if (!(await Ctor.install?.({ langs: [lang], processLocally: true }))) throw new Error(`Could not download the ${lang} speech model.`);
  }

  const rec = new Ctor();
  rec.lang = lang;
  rec.continuous = true;
  rec.interimResults = true;
  rec.processLocally = true;
  let wanted = true;
  let seen = 0;      // results.length at the last event
  let from = 0;      // results before this were heard before a jump, so are ignored

  rec.onresult = (e) => {
    seen = e.results.length;
    let text = '';
    for (let i = Math.max(from, seen - 3); i < seen; i++) text += ` ${e.results[i][0].transcript}`;
    const words = text.split(/\s+/).flatMap(normalise);
    if (words.length) onWords(words.slice(-HEARD));
  };
  rec.onerror = (e) => {
    if (e.error === 'no-speech' || e.error === 'aborted') return;
    wanted = false;
    onStatus(e.error === 'not-allowed' ? 'microphone blocked' : `speech error: ${e.error}`, true);
  };
  // Chrome ends a continuous session after a long silence; carry on until told to stop.
  rec.onend = () => { seen = 0; from = 0; if (wanted) try { rec.start(); } catch { /* already starting */ } };
  rec.start();
  onStatus(`listening · ${lang}`);

  return {
    lang,
    stop() { wanted = false; rec.abort(); },
    forget() { from = seen; },
  };
}
