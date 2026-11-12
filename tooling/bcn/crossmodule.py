"""References to other modules.

A programme's modules can be taken in any order, so nothing may assume another module has
already been studied ("as we saw in the previous module") or is still to come ("we'll cover
this in module 3"). Four kinds of reference are found:

  back      an earlier module: "previous module", "earlier in the programme", 上一个模块
  forward   a later one: "next module", "later in the programme", 下一个模块
  position  a module by its place in a sequence: "module 2", "the third module", 第二个模块
  named     another module by code (KV7017) or by its course-map title

The phrase patterns are built in; a programme adds its own with [validate].module_references
and [validate_zh].module_references (phrases, matched as words, reported as 'named').
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .tree import MODULE_RE

_ORD_EN = r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)"
_NUM_EN = r"(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)"
_PROG_EN = r"(?:programme|program|degree|course)"
_NUM_ZH = r"[一二三四五六七八九十\d]+"

PATTERNS: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    "en": [
        ("back", re.compile(r"\b(?:previous|preceding|prior|earlier|last|past|former)\s+modules?\b", re.I)),
        ("back", re.compile(r"\bthe\s+module\s+before\s+(?:this|that)\b", re.I)),
        ("back", re.compile(rf"\b(?:earlier|previously)\s+in\s+(?:the|this|your)\s+{_PROG_EN}\b", re.I)),
        ("back", re.compile(r"\b(?:another|other)\s+modules?\s+you(?:'ve|\s+have)\s+(?:already\s+)?(?:taken|studied|done|completed)\b", re.I)),
        ("forward", re.compile(r"\b(?:next|following|later|upcoming|subsequent|future|coming|final)\s+modules?\b", re.I)),
        ("forward", re.compile(r"\bthe\s+module\s+after\s+(?:this|that)\b", re.I)),
        ("forward", re.compile(rf"\blater\s+in\s+(?:the|this|your)\s+{_PROG_EN}\b", re.I)),
        ("position", re.compile(rf"\bmodule\s+{_NUM_EN}\b", re.I)),
        ("position", re.compile(rf"\b(?:the\s+)?{_ORD_EN}\s+module\b", re.I)),
    ],
    "zh": [
        ("back", re.compile(r"(?:上一?个?|前一个?|之前的?|先前的?|此前的?|以前的?)模块")),
        ("back", re.compile(r"(?:之前|先前|此前)(?:的)?课程中")),
        ("forward", re.compile(r"(?:下一?个?|后一个?|之后的?|后续的?|接下来的?|以后的?|未来的?)模块")),
        ("position", re.compile(rf"第{_NUM_ZH}个?模块")),
        ("position", re.compile(rf"模块{_NUM_ZH}")),
    ],
}

CODE_RE = re.compile(r"\b[A-Z]{2}\d{4}\b")

HINT = ("Modules can be taken in any order. Make the point stand on its own, or acknowledge it if the "
        "reference holds whichever order the learner takes them in.")


@dataclass
class Hit:
    kind: str     # back | forward | position | named
    match: str
    start: int


def other_modules(root: Path, own: str) -> dict[str, str | None]:
    """Every other module in the programme, code -> course-map title (None if it has none)."""
    from .coursemap import module_title
    out: dict[str, str | None] = {}
    try:
        names = sorted(p.name for p in root.iterdir() if p.is_dir() and MODULE_RE.match(p.name))
    except OSError:
        return out
    for name in names:
        if name != own:
            out[name] = module_title(root / name / "course-map.md")
    return out


def _phrase(phrase: str) -> re.Pattern[str]:
    words = [re.escape(w) for w in phrase.split()]
    return re.compile(r"(?<![\w])" + r"\s+".join(words) + r"(?![\w])", re.I)


def find(text: str, own: str, others: dict[str, str | None], lang: str, extra: list[str] | None = None) -> list[Hit]:
    """Each reference in text, earliest first; overlapping finds keep the first and longest."""
    hits: list[Hit] = []
    for kind, rx in PATTERNS.get(lang, []):
        hits += [Hit(kind, m.group(0), m.start()) for m in rx.finditer(text)]
    # Any module code but this one, whether or not that module is in this programme.
    hits += [Hit("named", m.group(0), m.start()) for m in CODE_RE.finditer(text) if m.group(0) != own]
    # Titles of two words or more: a one-word title ("Ethics") is too likely an ordinary word.
    names = [t for t in others.values() if t and len(t.split()) >= 2] + [p for p in (extra or []) if p.strip()]
    for name in names:
        hits += [Hit("named", m.group(0), m.start()) for m in _phrase(name).finditer(text)]
    hits.sort(key=lambda h: (h.start, -len(h.match)))
    out: list[Hit] = []
    end = -1
    for h in hits:
        if h.start >= end:
            out.append(h)
            end = h.start + len(h.match)
    return out


def describe(h: Hit) -> str:
    return {
        "back": f"refers back to another module ('{h.match}'), which the learner may not have taken yet",
        "forward": f"refers ahead to another module ('{h.match}'), which the learner may already have taken",
        "position": f"refers to a module by its place in a sequence ('{h.match}'), but modules have no fixed order",
        "named": f"names another module ('{h.match}'), whose place before or after this one is not fixed",
    }[h.kind]


def doc_diagnostics(path: Path, rel: str, own: str, others: dict[str, str | None], lang: str,
                    extra: list[str] | None = None, text: str | None = None):
    """DOC_MODULE_REFERENCE for a module document (course map, activity, assignment), one per
    reference, by line."""
    from .envelope import Diagnostic
    if text is None:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            return []
    out = []
    for i, line in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").splitlines()):
        for h in find(line, own, others, lang, extra):
            out.append(Diagnostic("DOC_MODULE_REFERENCE", f"{rel} {describe(h)}.", level="error", file=rel, line=i + 1,
                                  hint=HINT, data={"match": h.match, "kind": h.kind}))
    return out
