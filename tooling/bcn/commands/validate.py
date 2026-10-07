"""bcn validate: structure and content checks."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from .. import coursemap, fsutil
from ..config import Config, load
from ..envelope import Diagnostic, Envelope, TopicResult
from ..progress import TopicProgress
from ..rules import validate_topic
from ..runner import run_topics
from ..tree import Target, Topic

HELP = "structure and content checks"


def add_args(p: argparse.ArgumentParser) -> None:
    pass


def check_topic(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config, lang: str) -> None:
    parsed, diags = validate_topic(cfg, t, lang)
    r.diagnostics.extend(diags)
    if parsed is not None:
        r.extra["slides"] = len(parsed.slides)
        if lang == "en":
            try:
                minutes = int(parsed.front.get("minutes", 0))
            except ValueError:
                minutes = 0
            r.extra["words"] = parsed.narration_words
            r.extra["target_words"] = minutes * cfg["validate"]["words_per_minute"]


def module_documents(target: Target, override_path: Path | None = None, override_text: str | None = None) -> list[Diagnostic]:
    """course-map.md, activity.md and assignment-*.md, against their lighter rules.

    override_path/override_text let a dry-run see what *unsaved* edited text would do to the
    whole module's checks (e.g. editing course-map.md's outcomes affects every activity's
    DOC_OUTCOME_UNKNOWN) — the same reason validate_topic() takes a text override for topic.md.
    """
    if target.level == "topic":
        return []
    out: list[Diagnostic] = []
    for m in target.modules:
        mdir = target.root / m
        cfg = load(target.root, mdir)
        docs = cfg["documents"]
        cm_path = mdir / "course-map.md"
        cm_text = override_text if override_path == cm_path else None
        cm = coursemap.load_course_map(mdir, docs["course_map_headings"], text=cm_text)
        out.extend(cm.diagnostics)
        units = sorted({t.unit for t in target.topics if t.module == m} | ({u for u in cm.units} if target.level != "unit" else set()))
        if target.level == "unit":
            units = [target.rel.split("/")[1]]
        for u in units:
            act = mdir / u / "activity.md"
            act_text = override_text if override_path == act else None
            if act.is_file() or act_text is not None:
                out.extend(coursemap.validate_activity(act, f"{m}/{u}/activity.md", docs["activity_headings"], cm.outcomes,
                                                       m, u, docs["activity_types"], text=act_text))
        if target.level in ("root", "module"):
            names = {a.name for a in mdir.glob("assignment-*.md")}
            if override_path and override_path.parent == mdir and re.match(r"assignment-\d+\.md", override_path.name):
                names.add(override_path.name)
            for name in sorted(names):
                a = mdir / name
                a_text = override_text if override_path == a else None
                out.extend(coursemap.validate_doc(a, f"{m}/{name}", docs["assignment_headings"], cm.outcomes, text=a_text))
    return out


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    lang = args.lang

    def fn(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config) -> None:
        check_topic(t, r, tp, cfg, lang)

    run_topics(env, target, "validate", lang, fn, jobs=args.jobs, report_unexpected=True)
    if lang == "en":
        docs = module_documents(target)
        env.diagnostics.extend(docs)
        for m in target.modules if target.level in ("root", "module") else []:
            mod_diags = [d for d in docs if (d.file or "").startswith(m + "/")]
            fsutil.write_json(target.root / m / "build" / "validate.json", {
                "tool": "validate", "schema": 1, "target": m,
                "ok": not any(d.level == "error" for d in mod_diags), "started": env.started,
                "results": [], "artifacts": [], "diagnostics": [d.to_json() for d in mod_diags]})
