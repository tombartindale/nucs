#!/usr/bin/env python3
"""Rewrite an auto-generated SRT so its text matches the original script.

Keeps every cue's index and timestamps. Replaces cue text with the script's
wording (spelling, punctuation, capitalisation), using word-level fuzzy
sequence alignment. Script text that was never spoken (no audio match) is
dropped from the SRT and reported, never crammed into a cue.

Usage:
  align_srt.py SCRIPT.md IN.srt OUT.srt [--say-prefix "> **Say:**"] [--plain]
                [--max-line 42] [--max-drop-run 5] [--report report.json]
  --plain : treat SCRIPT as plain text (use whole file) instead of extracting
            blockquote "Say:" lines.
"""
import re, sys, json, argparse, difflib

def norm(w): return re.sub(r'[^a-z0-9]', '', w.lower())

def load_script(path, prefix, plain):
    txt = open(path, encoding='utf-8').read()
    if plain:
        return txt.split()
    # Collect blockquote blocks (consecutive lines starting with ">"), which may
    # wrap over several lines. Keep blocks that start with the Say prefix.
    blocks, cur = [], []
    for l in txt.splitlines() + ['']:
        if l.lstrip().startswith('>'):
            cur.append(re.sub(r'^\s*>\s?', '', l).rstrip())
        elif cur:
            blocks.append(cur); cur = []
    def join(lines):
        out = ''
        for l in lines:
            l = l.strip()
            if not l: continue
            # a word hyphenated across a line break ("rule-\nbased") rejoins without a space
            out = (out + l) if out.endswith('-') else (out + ' ' + l if out else l)
        return out
    pre = prefix.strip() if prefix else ''
    say = [join(b) for b in blocks if join(b).startswith(pre)] if pre else []
    say = [re.sub('^' + re.escape(pre) + r'\s*', '', x) for x in say]
    if not say:  # fall back to any blockquote text
        say = [join(b) for b in blocks]
    if not say:
        sys.exit('No blockquote/Say blocks found; rerun with --plain.')
    return ' '.join(say).split()

def load_srt(path):
    raw = open(path, encoding='utf-8-sig', newline='').read()
    cues = []
    for b in [b for b in re.split(r'\r?\n\r?\n', raw.strip()) if b.strip()]:
        ls = b.splitlines()
        cues.append((ls[1], ' '.join(ls[2:])))
    return cues

def sim(a, b):
    if a == b: return 2.0
    r = difflib.SequenceMatcher(None, a, b).ratio()
    return 2 * r - 1.0 if r > 0.5 else -1.0

LATE_BONUS = 0.0  # tested as a retake tie-break; it only changed which takes got mixed, so it is off

def align(A, B, gap=-0.6):
    n, m = len(A), len(B)
    S = [[0.0] * (m + 1) for _ in range(n + 1)]
    T = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1): S[i][0] = i * gap; T[i][0] = 1
    for j in range(1, m + 1): S[0][j] = j * gap; T[0][j] = 2
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            S[i][j], T[i][j] = max((S[i-1][j-1] + sim(A[i-1], B[j-1]) + LATE_BONUS * i / n, 0),
                                   (S[i-1][j] + gap, 1), (S[i][j-1] + gap, 2))
    return T

def wrap(words, width):
    s = ' '.join(words)
    if len(s) <= width: return s
    best = None
    for k in range(1, len(words)):
        a, b = ' '.join(words[:k]), ' '.join(words[k:])
        if best is None or max(len(a), len(b)) < best[0]: best = (max(len(a), len(b)), a, b)
    return best[1] + '\r\n' + best[2]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('script'); ap.add_argument('srt'); ap.add_argument('out')
    ap.add_argument('--say-prefix', default='**Say:**')
    ap.add_argument('--plain', action='store_true')
    ap.add_argument('--max-line', type=int, default=42)
    ap.add_argument('--max-drop-run', type=int, default=5)
    ap.add_argument('--report')
    a = ap.parse_args()

    sw = load_script(a.script, a.say_prefix, a.plain)
    cues = load_srt(a.srt)
    tw, owner = [], []
    for i, (_, x) in enumerate(cues):
        for w in x.split(): tw.append(w); owner.append(i)
    A, B = [norm(w) for w in tw], [norm(w) for w in sw]
    T = align(A, B)
    i, j, m = len(A), len(B), len(B)
    assign = [None] * m
    while i > 0 or j > 0:
        t = T[i][j]
        if i > 0 and j > 0 and t == 0: assign[j-1] = owner[i-1]; i -= 1; j -= 1
        elif i > 0 and (j == 0 or t == 1): i -= 1
        else: j -= 1

    dropped, k = [], 0
    while k < m:
        if assign[k] is None:
            e = k
            while e < m and assign[e] is None: e += 1
            prev = assign[k-1] if k > 0 else None
            nxt = assign[e] if e < m else None
            if e - k > a.max_drop_run:
                dropped.append(' '.join(sw[k:e])); tgt = -1
            elif prev is None: tgt = nxt
            elif nxt is not None and re.search(r'[,.?!:;]$', sw[k-1]): tgt = nxt
            else: tgt = prev
            for x in range(k, e): assign[x] = tgt
            k = e
        else: k += 1
    for k in range(1, m):
        if assign[k] != -1 and assign[k-1] != -1 and assign[k] < assign[k-1]: assign[k] = assign[k-1]

    out, empty, changed = [], [], []
    for ci, (t, x) in enumerate(cues):
        words = [sw[k] for k in range(m) if assign[k] == ci]
        if not words: empty.append(ci + 1); continue
        if norm(' '.join(words)) != norm(x): changed.append({'cue': ci+1, 'before': x, 'after': ' '.join(words)})
        out.append((t, words))
    res = [f"{n}\r\n{t}\r\n{wrap(w, a.max_line)}\r\n" for n, (t, w) in enumerate(out, 1)]
    open(a.out, 'w', encoding='utf-8-sig', newline='').write('\r\n'.join(res) + '\r\n')

    def secs(ts):
        h, mi, r = ts.split(':'); sc, ms = r.split(','); return int(h)*3600+int(mi)*60+int(sc)+int(ms)/1000
    flags = []
    for n, (t, w) in enumerate(out):
        a_, b_ = t.split(' --> ')
        gb = secs(a_) - secs(out[n-1][0].split(' --> ')[1]) if n else 0
        ga = secs(out[n+1][0].split(' --> ')[0]) - secs(b_) if n + 1 < len(out) else 0
        if len(w) <= 2 and (gb > 3 or ga > 3):
            flags.append({'cue': n+1, 'time': a_, 'text': ' '.join(w), 'gap_before_s': round(gb, 1), 'gap_after_s': round(ga, 1)})
    rep = {'review_flags': flags, 'cues_in': len(cues), 'cues_out': len(out), 'cues_changed': len(changed),
           'script_text_not_spoken': dropped, 'cues_with_no_script_match_removed': empty,
           'changes': changed}
    if a.report: json.dump(rep, open(a.report, 'w'), indent=2, ensure_ascii=False)
    print(f"cues in/out: {len(cues)}/{len(out)}  changed: {len(changed)}")
    for d in dropped: print('NOT SPOKEN (dropped):', d)
    if empty: print('REMOVED empty cues:', empty)
    for f in flags: print(f"REVIEW cue {f['cue']} @ {f['time'][:12]}: '{f['text']}' (gap before {f['gap_before_s']}s, after {f['gap_after_s']}s) - likely retake")

if __name__ == '__main__': main()
