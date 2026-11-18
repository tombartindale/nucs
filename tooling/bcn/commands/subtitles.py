"""bcn subtitles: convert or normalise the supplied SRT. Never re-times anything.

English: edit/master.srt passes through untouched unless reviewed corrections
exist or line normalisation is switched on, in which case only cue text changes.
Mandarin: the returned SRT may split, merge and re-time cues freely; only lines
left in English are flagged.
"""

from __future__ import annotations

import argparse
import re

from .. import fsutil, reviewfile, srt
from ..config import Config
from ..envelope import Diagnostic, Envelope, TopicResult
from ..progress import TopicProgress
from ..runner import require_input, require_step, run_topics, try_skip
from ..tree import Target, Topic
from ..codes import SHEET_CODES

HELP = "convert or normalise the supplied SRT"


def add_args(p: argparse.ArgumentParser) -> None:
    pass


def wrap(text: str, width: int, by_chars: bool) -> str:
    flat = re.sub(r"\s*\n\s*", "" if by_chars else " ", text).strip()
    if len(flat) <= width:
        return flat
    lines: list[str] = []
    if by_chars:
        # Chinese breaks without spaces: cut on character count, preferring punctuation.
        cur = ""
        for ch in flat:
            cur += ch
            if len(cur) >= width or (ch in "，。？！；：、" and len(cur) >= width * 0.6):
                lines.append(cur)
                cur = ""
        if cur:
            lines.append(cur)
    else:
        cur = ""
        for w in flat.split():
            if cur and len(cur) + 1 + len(w) > width:
                lines.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        lines.append(cur)
    return "\n".join(lines)


def apply_corrections(t: Topic, cues: list[srt.Cue], r: TopicResult) -> int:
    decisions = reviewfile.load(t)["transcripts"]
    applied = 0
    for dec in decisions.values():
        if dec.get("decision") != "correct" or not dec.get("text") or not dec.get("srt") or not dec.get("cue"):
            continue
        if dec.get("applied"):
            # Already written directly into the SRT when the decision was made (the Caption
            # Editor does this) - there is nothing left to substitute. Skip entirely rather than
            # searching for the old "heard" span: it's often a short, common fragment ("test",
            # "model") that can resurface by coincidence in later, unrelated text, and regex-
            # substituting the whole decision text in over it would corrupt an already-correct line.
            continue
        heard = dec["srt"]
        pattern = r"\s+".join(re.escape(w) for w in heard.split())
        done = False
        for idx in (dec["cue"] - 1, dec["cue"] - 2, dec["cue"]):
            if 0 <= idx < len(cues):
                new, n = re.subn(pattern, dec["text"], cues[idx].text, count=1)
                if n:
                    cues[idx].text = new
                    done = True
                    applied += 1
                    r.diagnostics.append(Diagnostic("SRT_CORRECTION", f"Cue {idx + 1}: \"{heard}\" corrected to \"{dec['text']}\".",
                                                    topic=t.id, lang="en", file="edit/master.srt", line=cues[idx].line))
                    break
        if not done:
            r.diagnostics.append(Diagnostic("SRT_CORRECTION", f"Could not find \"{heard}\" near cue {dec['cue']} to correct it.",
                                            level="warn", topic=t.id, lang="en", file="review.json",
                                            hint="The SRT may have changed since the review. Re-run cues and review again."))
    return applied


def _write_if_changed(dest, data: bytes) -> None:
    if dest.is_file() and dest.read_bytes() == data:
        return
    fsutil.write_bytes(dest, data)


def subtitles_topic(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config, lang: str, force: bool,
                    stable: bool = True) -> None:
    sc = cfg["subtitles"]
    fmt = sc["format"]
    exts = {"srt": ["srt"], "vtt": ["srt", "vtt"], "both": ["srt", "vtt"]}.get(fmt, ["srt"])
    src = t.srt(lang)
    rel = str(src.relative_to(t.dir))
    require_input(t, src, lang, stable=stable)
    inputs = [src, t.root / "programme.toml"]
    if lang == "en":
        inputs.append(t.src("en"))
        require_step(t, "cues", "en", [t.src("en"), t.srt("en"), t.video], "bcn cues", ignore=SHEET_CODES)
    else:
        require_input(t, t.srt("en"), "en")
        inputs.append(t.srt("en"))
        require_step(t, "validate", "zh", [t.src("zh"), t.src("en")], "bcn validate --lang zh")
    outputs = [t.subtitle_out(lang, e) for e in exts]
    # Freshness is judged on the step file: outputs are only rewritten when their
    # content changes, so a no-op review decision does not ripple into package.
    if lang == "en":
        current = sorted(k for k, v in reviewfile.load(t)["transcripts"].items() if v.get("decision") == "correct")
        prev = fsutil.read_json(t.step_file("subtitles", lang)) or {}
        force = force or ((prev.get("results") or [{}])[0].get("corrections", []) != current)
    if all(o.is_file() for o in outputs) and try_skip(t, r, "subtitles", lang, [], inputs, force):
        return

    cues = srt.parse_file(src, rel)
    changed = False

    if lang == "zh":
        # Translators split, merge and re-time cues to suit Mandarin reading speed, and that
        # is expected: the Mandarin SRT is only ever a subtitle track (slide timing comes
        # from the English cue sheet), and the composed video gets a human QA pass. So cue
        # count and timings are not compared with the English. What is still worth catching
        # is a line left in English, matched against any English cue rather than by position.
        en = srt.parse_file(t.srt("en"), "edit/master.srt")
        english = {c.plain for c in en if re.search(r"[A-Za-z]{3,}", c.plain)}
        for a in cues:
            if a.plain in english:
                r.diagnostics.append(Diagnostic("SRT_ZH_UNTRANSLATED", f"Cue {a.index} has the English text unchanged.",
                                                topic=t.id, lang=lang, file=rel, line=a.line))
    else:
        if apply_corrections(t, cues, r):
            changed = True

    if sc["normalise"]:
        width = sc["zh_max_line_chars"] if lang == "zh" else sc["max_line_chars"]
        for c in cues:
            new = wrap(c.text, width, by_chars=(lang == "zh"))
            if new != c.text:
                c.text = new
                changed = True
                if len(new.split("\n")) > sc["max_lines"]:
                    r.diagnostics.append(Diagnostic("SRT_LINE_LENGTH", f"Cue {c.index} needs {len(new.splitlines())} lines at {width} characters; timings are not changed to fit.",
                                                    level="warn", topic=t.id, lang=lang, file=rel, line=c.line))
    for c in cues:
        if c.end - c.start > sc["max_cue_seconds"]:
            r.diagnostics.append(Diagnostic("SRT_CUE_DURATION", f"Cue {c.index} lasts {c.end - c.start:.1f}s; the maximum is {sc['max_cue_seconds']}s.",
                                            topic=t.id, lang=lang, file=rel, line=c.line))

    out_srt = t.subtitle_out(lang, "srt")
    # Unchanged, the file passes through byte for byte.
    _write_if_changed(out_srt, srt.to_srt(cues).encode("utf-8") if changed else src.read_bytes())
    r.artifacts.append(Envelope.artifact_for(t.root, out_srt, "subtitles"))
    if "vtt" in exts:
        out_vtt = t.subtitle_out(lang, "vtt")
        _write_if_changed(out_vtt, srt.to_vtt(cues).encode("utf-8"))
        r.artifacts.append(Envelope.artifact_for(t.root, out_vtt, "subtitles"))
    r.extra.update({"cues": len(cues), "modified": changed, "format": fmt})
    if lang == "en":
        r.extra["corrections"] = sorted(k for k, v in reviewfile.load(t)["transcripts"].items() if v.get("decision") == "correct")
    tp.update(100, "done")


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    def fn(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config) -> None:
        subtitles_topic(t, r, tp, cfg, args.lang, args.force)

    run_topics(env, target, "subtitles", args.lang, fn, jobs=args.jobs)
