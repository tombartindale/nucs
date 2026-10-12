"""bcn translation: the batch handover to translation and back.

--export  For every topic in scope (all or nothing), put the English SRT (reviewed corrections
          applied) and the narration-stripped slide markdown in a batch folder
          and zip under translation/exports/, with a manifest, and record the
          export in each topic's translation.json. In-scope assignment-N.md and
          unit activity.md documents ride along in the same batch, raw text
          (no stripping) under the flat name bcn sync already knows for them
          (KV7016-assignment-1.md, KV7016-U01-activity.md) — ready the same
          way a module document is ready anywhere else: no error from bcn
          validate's module-document checks.
--import  Take returned <topic_id>.zh.md / .zh.srt, <module>-assignment-N.zh.md and
          <module>-<unit>-activity.zh.md files from a folder or zip, place them
          as topic.zh.md/edit/master.zh.srt, assignment-N.zh.md and
          activity.zh.md, then run validate --lang zh (and subtitles --lang zh
          for topics) across the batch and report every failure together, so
          they go back to the translator in one message. A translated quiz is
          checked for parity against the English too: same questions, same
          correct answer(s) — only the wording may differ.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from .. import fsutil
from ..coursemap import load_course_map, validate_activity, validate_doc
from ..config import load
from ..envelope import Diagnostic, Envelope, Fail, TopicResult, sha256_file, utcnow
from ..markdown import parse
from ..runner import run_topics
from ..state import TopicState, module_context
from ..sync import local_to_remote, remote_to_local
from ..tree import Target, Topic, is_noise, topic_from_id
from . import subtitles as subtitles_cmd
from . import transfer as transfer_cmd
from . import validate as validate_cmd

DOC_ZH_RE = re.compile(r"^([A-Z]{2}\d{4})-(?:assignment-\d+|U\d{2}-activity)\.zh\.md$")

HELP = "export topics for translation, or import what comes back"


def add_args(p: argparse.ArgumentParser) -> None:
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--export", action="store_true", help="export the topics beneath the path")
    g.add_argument("--import", dest="source", metavar="DIR_OR_ZIP", help="import returned files")


def _doc_scope(root: Path, module: str, unit: str | None) -> list[str]:
    """English assignment-N.md / unit activity.md names (relative to the module) in scope —
    the same unit-narrowing bcn transfer's _module_files() uses: assignment-N.md only at
    module/root scope, a unit's activity.md at any scope that reaches that unit."""
    mdir = root / module
    names: list[str] = []
    if unit is None:
        names.extend(sorted(p.name for p in mdir.glob("assignment-*.md") if not p.name.endswith(".zh.md")))
    unit_dirs = [mdir / unit] if unit else sorted(mdir.glob("U[0-9][0-9]"))
    for d in unit_dirs:
        if (d / "activity.md").is_file():
            names.append(f"{d.name}/activity.md")
    return names


def _export(env: Envelope, target: Target) -> None:
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    scope = target.rel.replace("/", "-") if target.rel != "." else "programme"
    batch = f"{stamp}-{scope}"
    exports = target.root / "translation" / "exports"
    n = 2
    while (exports / batch).exists() or (exports / f"{batch}.zip").exists():
        batch = f"{stamp}-{scope}-{n}"
        n += 1
    out_dir = exports / batch
    ready: list[tuple[Topic, TopicResult]] = []
    for t in target.topics:
        r = TopicResult(t.id, t.rel)
        env.results.append(r)
        st = TopicState(t, module_context(t.root, t.module)).compute()
        en = st["en"]
        reasons = []
        if not en["steps"].get("validate", {}).get("ok") or not en["steps"].get("validate", {}).get("fresh"):
            reasons.append("the English has not passed validate")
        subs = en["steps"].get("subtitles", {})
        if not (subs.get("ok") and subs.get("fresh")) or not t.subtitle_out("en", "srt").is_file():
            reasons.append("the English SRT has not been through bcn subtitles")
        if st["unreviewed_mistranscriptions"]:
            reasons.append(f"{st['unreviewed_mistranscriptions']} suspected mis-transcriptions are unreviewed; the translator would inherit them")
        if reasons:
            r.ok = False
            r.diagnostics.append(Diagnostic("XL_NOT_READY", f"{t.id} is not ready to export: {'; '.join(reasons)}.", topic=t.id, lang="zh"))
            continue
        ready.append((t, r))

    unit = target.rel.split("/")[1] if target.level in ("unit", "topic") else None
    doc_ready: list[tuple[str, str, Path, TopicResult]] = []
    for m in target.modules:
        mdir = target.root / m
        diags = validate_cmd.module_documents(Target(target.root, mdir, "module", m, [m], [], []))
        for name in _doc_scope(target.root, m, unit):
            rel = f"{m}/{name}"
            r = TopicResult(rel, rel)
            env.results.append(r)
            if any(d.level == "error" for d in diags if d.file == rel):
                r.ok = False
                r.diagnostics.append(Diagnostic("XL_NOT_READY", f"{rel} is not ready to export: it has not passed validate.",
                                                file=rel, lang="zh"))
                continue
            doc_ready.append((m, name, mdir / name, r))

    if not (ready or doc_ready) or (len(ready) + len(doc_ready)) < len(env.results):
        # A batch is a handover: all of the scope goes, or none of it does.
        if ready or doc_ready:
            not_ready = len(env.results) - len(ready) - len(doc_ready)
            env.diagnostics.append(Diagnostic("XL_NOT_READY", f"Nothing was exported: {not_ready} of {len(env.results)} items are not ready.",
                                              hint="Fix them, or export a narrower scope."))
        return
    work = Path(tempfile.mkdtemp(prefix=".export-", dir=target.root))
    try:
        files = []
        for t, _r in ready:
            p = parse(t.src("en"), "topic.md", t.id)
            md = work / f"{t.id}.md"
            md.write_text(p.stripped_file(), encoding="utf-8")
            srt = work / f"{t.id}.en.srt"
            shutil.copyfile(t.subtitle_out("en", "srt"), srt)
            for f in (md, srt):
                files.append({"topic": t.id, "name": f.name, "bytes": f.stat().st_size, "sha256": sha256_file(f)})
        for m, name, path, _r in doc_ready:
            remote_name = local_to_remote(m, name)
            dest = work / remote_name
            shutil.copyfile(path, dest)
            files.append({"topic": None, "document": f"{m}/{name}", "name": remote_name,
                          "bytes": dest.stat().st_size, "sha256": sha256_file(dest)})
        manifest = {
            "batch": batch, "created": utcnow(), "topics": [t.id for t, _ in ready],
            "documents": [f"{m}/{name}" for m, name, _, _ in doc_ready], "files": files,
            "return": "For each topic return <topic_id>.zh.md (same slides, same breaks, lang: zh, no Say blocks, "
                      "with the title: line and every slide heading translated) "
                      "and <topic_id>.zh.srt (same cue count, identical timings, translated text). "
                      "For each assignment or activity document, return its name with .zh added before .md "
                      "(KV7016-assignment-1.md -> KV7016-assignment-1.zh.md): the same structure, translated — "
                      "a quiz keeps the same questions, in the same order, with the same correct answer(s).",
        }
        fsutil.write_json(work / "manifest.json", manifest)
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(work), out_dir)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    zpath = out_dir.with_suffix(".zip")
    with fsutil.atomic_path(zpath) as tmp:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(out_dir.iterdir()):
                z.write(f, f"{batch}/{f.name}")
    for t, r in ready:
        rec = fsutil.read_json(t.translation_file) or {}
        rec.setdefault("exports", []).append({
            "batch": batch, "at": manifest["created"],
            "files": [f for f in files if f["topic"] == t.id],
        })
        fsutil.write_json(t.translation_file, rec)
        r.extra["batch"] = batch
        r.diagnostics.append(Diagnostic("XL_EXPORTED", f"{t.id} exported in batch {batch}.", topic=t.id, lang="zh"))
    for m, name, _path, r in doc_ready:
        r.extra["batch"] = batch
        r.diagnostics.append(Diagnostic("XL_EXPORTED", f"{m}/{name} exported in batch {batch}.", file=f"{m}/{name}", lang="zh"))
    env.artifacts.append(env.artifact(zpath, "translation-export"))
    env.extra["batch"] = batch
    env.extra["folder"] = env.rel(out_dir)
    env.extra["zip"] = env.rel(zpath)


def _import(env: Envelope, target: Target, source: str, args: argparse.Namespace) -> None:
    src = Path(source).expanduser()
    if not src.exists():
        raise Fail("FS_MISSING", f"{source} does not exist.")
    tmp = None
    try:
        if src.is_file() and src.suffix.lower() == ".zip":
            tmp = Path(tempfile.mkdtemp(prefix=".import-", dir=target.root))
            with zipfile.ZipFile(src) as z:
                for member in z.namelist():
                    name = Path(member).name
                    if name and not member.endswith("/") and not is_noise(name) and "__MACOSX" not in member:
                        (tmp / name).write_bytes(z.read(member))
            base = tmp
        elif src.is_dir():
            base = src
        else:
            raise Fail("USAGE", f"{source} must be a folder or a .zip.")
        found: dict[str, dict[str, Path]] = {}
        doc_found: dict[str, Path] = {}  # "<module>/<local_rel>" -> the returned .zh.md file
        for f in sorted(base.rglob("*")):
            if not f.is_file() or is_noise(f.name) or "__MACOSX" in f.parts:
                continue
            m = re.match(r"^([A-Z]{2}\d{4}-U\d{2}-T\d{2})\.zh\.(md|srt)$", f.name)
            if m:
                found.setdefault(m.group(1), {})[m.group(2)] = f
                continue
            dm = DOC_ZH_RE.match(f.name)
            local_rel = remote_to_local(dm.group(1), f.name) if dm else None
            if dm and local_rel:
                doc_found[f"{dm.group(1)}/{local_rel}"] = f
                continue
            if f.name != "manifest.json":
                env.diagnostics.append(Diagnostic("XL_UNKNOWN_FILE",
                                                  f"'{f.name}' is not a <topic_id>.zh.md/.zh.srt, <module>-assignment-N.zh.md, "
                                                  f"or <module>-<unit>-activity.zh.md file; ignored.", file=f.name))
        in_scope = {t.id for t in target.topics}
        placed: list[Topic] = []
        results: dict[str, TopicResult] = {}
        for tid, kinds in sorted(found.items()):
            t = topic_from_id(target.root, tid)
            r = TopicResult(tid, t.rel)
            results[tid] = r
            env.results.append(r)
            if not t.dir.is_dir() or (target.level != "root" and tid not in in_scope):
                r.ok = False
                r.diagnostics.append(Diagnostic("XL_UNKNOWN_FILE", f"{tid} is not a topic under {target.rel}; its files were not placed.", topic=tid, lang="zh"))
                continue
            actions = {}
            for kind, f in kinds.items():
                dest = t.src("zh") if kind == "md" else t.srt("zh")
                data = f.read_bytes()
                if dest.is_file() and dest.read_bytes() == data:
                    actions[kind] = "unchanged"
                    continue
                actions[kind] = "replaced" if dest.exists() else "placed"
                fsutil.write_bytes(dest, data)
                r.diagnostics.append(Diagnostic("XL_IMPORTED", f"{actions[kind].capitalize()} {dest.relative_to(t.root)}.", topic=tid, lang="zh",
                                                file=str(dest.relative_to(t.dir))))
            missing = {"md", "srt"} - set(kinds)
            if missing:
                r.diagnostics.append(Diagnostic("FS_MISSING", f"The return for {tid} has no {' or '.join('.zh.' + k for k in sorted(missing))} file.",
                                                topic=tid, lang="zh"))
            r.extra["files"] = actions
            placed.append(t)
        placed_docs: list[tuple[str, str]] = []  # (module, local_rel), e.g. ("KV7015", "assignment-1.zh.md")
        doc_results: dict[str, TopicResult] = {}
        for key, f in sorted(doc_found.items()):
            module, local_rel = key.split("/", 1)
            rel = f"{module}/{local_rel}"
            r = TopicResult(rel, rel)
            doc_results[rel] = r
            env.results.append(r)
            if not transfer_cmd._in_scope(target, module, local_rel):
                r.ok = False
                r.diagnostics.append(Diagnostic("XL_UNKNOWN_FILE", f"{rel} is outside {target.rel}; it was not placed.", file=rel, lang="zh"))
                continue
            dest = target.root / module / local_rel
            data = f.read_bytes()
            if dest.is_file() and dest.read_bytes() == data:
                r.extra["action"] = "unchanged"
            else:
                r.extra["action"] = "replaced" if dest.exists() else "placed"
                fsutil.write_bytes(dest, data)
                r.diagnostics.append(Diagnostic("XL_IMPORTED", f"{r.extra['action'].capitalize()} {rel}.", file=rel, lang="zh"))
            placed_docs.append((module, local_rel))
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    if not placed and not placed_docs:
        return
    # Check the whole batch at once: parity first, then SRT timings.
    sub = Target(target.root, target.path, target.level, target.rel, target.modules, placed, [])
    for step, fn in (
        ("validate", lambda t, r, tp, cfg: validate_cmd.check_topic(t, r, tp, cfg, "zh")),
        ("subtitles", lambda t, r, tp, cfg: subtitles_cmd.subtitles_topic(t, r, tp, cfg, "zh", True, stable=False)),
    ):
        step_env = Envelope(step, target.rel, target.root)
        run_topics(step_env, sub, step, "zh", fn)
        for sr in step_env.results:
            r = results[sr.topic]
            r.extra[f"{step}_ok"] = sr.ok
            r.diagnostics.extend(d for d in sr.diagnostics if d.level != "info")
    for r in results.values():
        r.ok = not any(d.level == "error" for d in r.diagnostics)

    # Each returned assignment/activity document, checked against the zh headings config and
    # (for a quiz) against its English sibling's structure — scoped to just this one document,
    # so (unlike check_topic's whole-topic validate) every error it finds is relevant to report.
    for module, local_rel in placed_docs:
        rel = f"{module}/{local_rel}"
        r = doc_results[rel]
        mdir = target.root / module
        docs_cfg = load(target.root, mdir)["documents"]
        cm = load_course_map(mdir, docs_cfg["course_map_headings"])
        path = mdir / local_rel
        if local_rel.endswith("activity.zh.md"):
            u = local_rel.split("/")[0]
            diags = validate_activity(path, rel, docs_cfg["activity_headings"], cm.outcomes, module, u,
                                      docs_cfg["activity_types"], lang="zh", required_zh=docs_cfg["activity_headings_zh"])
        else:
            diags = validate_doc(path, rel, docs_cfg["assignment_headings"], cm.outcomes, lang="zh",
                                 required_zh=docs_cfg["assignment_headings_zh"])
        r.diagnostics.extend(diags)
        r.ok = not any(d.level == "error" for d in diags)

    env.extra["return_to_translator"] = [
        {"topic": d.topic, "code": d.code, "message": d.message}
        for r in results.values() for d in r.diagnostics
        if d.level == "error" and d.code in ("MD_PARITY_COUNT", "MD_PARITY_BREAK", "MD_SAY_IN_ZH", "MD_ZH_UNTRANSLATED",
                                             "SRT_ZH_CUE_COUNT", "SRT_ZH_TIMING", "FS_MISSING")
    ] + [
        {"topic": r.topic, "code": d.code, "message": d.message}
        for r in doc_results.values() for d in r.diagnostics if d.level == "error"
    ]


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    if args.export:
        _export(env, target)
    else:
        _import(env, target, args.source, args)
