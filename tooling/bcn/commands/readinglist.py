"""bcn readinglist: every unit's Reading paragraph from course-map.md, pulled out into
one markdown list per module.

course-map.md is hand-authored free text; a unit's '**Reading.** ...' paragraph is not a
citation format bcn can parse, so this does not try to tell a real second citation apart
from a note like "revisited" or "still to be identified" — it just splits each paragraph on
sentence boundaries (see coursemap.split_citations) and lists them under their unit. A
human skimming the result can tell the difference far more easily than bcn ever could from
the text alone.

Written to <module>/build/reading-list.md; skipped if already newer than course-map.md.
"""

from __future__ import annotations

import argparse

from .. import fsutil
from ..coursemap import load_course_map, split_citations
from ..envelope import Diagnostic, Envelope, TopicResult
from ..tree import Target
from .status import _module_title

HELP = "every unit's Reading paragraph from the module map, as one list"


def add_args(p: argparse.ArgumentParser) -> None:
    pass


def _render(module: str, title: str | None, cm) -> str:
    heading = f"{module}{' — ' + title if title else ''}"
    lines = [f"# {heading} — Reading list", ""]
    for unit in sorted(cm.readings):
        reading = cm.readings[unit]
        citations = split_citations(reading)
        if not citations:
            continue
        unit_title = cm.units.get(unit, "")
        lines.append(f"## {unit}{' — ' + unit_title if unit_title else ''}")
        lines.append("")
        lines.extend(f"- {c}" for c in citations)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    for m in target.modules:
        rel = f"{m}/course-map.md"
        src = target.root / rel
        mdir = target.root / m
        r = TopicResult(m, m)
        if not src.is_file():
            r.diagnostics.append(Diagnostic("DOC_MISSING", f"{rel} does not exist.", file=rel))
            r.ok = False
            env.results.append(r)
            continue
        out = mdir / "build" / "reading-list.md"
        if not args.force and fsutil.is_fresh([out], [src]):
            r.skipped = True
            r.extra["path"] = str(out.relative_to(target.root))
            env.results.append(r)
            continue
        cm = load_course_map(mdir)
        title = _module_title(src)
        text = _render(m, title, cm)
        fsutil.write_text(out, text)
        n = sum(len(split_citations(reading)) for reading in cm.readings.values())
        r.extra["path"] = str(out.relative_to(target.root))
        r.extra["units"] = len(cm.readings)
        r.extra["entries"] = n
        r.artifacts.append(Envelope.artifact_for(target.root, out, "readinglist"))
        if not cm.readings:
            r.diagnostics.append(Diagnostic("READINGLIST_EMPTY", f"No unit in {rel} has a '**Reading.**' paragraph.", file=rel))
        env.results.append(r)
