"""bcn transfer: a one-shot whole-tree handover, in or out, as a single zip.

--export  Zip every recognised content file in scope — course-map.md, assets.md,
          reading-list.md, assignment-N.md, activity.md, topic.md/topic.zh.md by
          default; add --media for topic assets/ and the edited video/subtitles
          too — under transfer/exports/, using the names bcn sync already knows
          (flat "KV7016-U01-T01.md" by default, or --nested for the pipeline's
          own layout).
--import  Take a zip or folder of files named either way, place each one at the
          path bcn sync's naming rules say it belongs at, and validate every
          topic touched. A file whose name matches no known pattern is reported,
          not guessed at. A file that already exists with different content is
          never overwritten: the diff comes back instead, exactly as bcn intake
          already does for topic.md.

This exists for the same handover bcn sync used to do against a live OneDrive
folder, but as a one-time transfer with no persistent synced state: a content
creator or partner gets or sends one zip, not a live two-way folder.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import shutil
import tempfile
import zipfile
from pathlib import Path

from .. import fsutil
from ..envelope import Diagnostic, Envelope, Fail, TopicResult, sha256_file, utcnow
from ..runner import run_topics
from ..sync import local_to_remote, remote_to_local
from ..tree import Target, is_noise
from ..tree import resolve as resolve_target
from . import validate as validate_cmd

HELP = "export or import a whole module/unit as a single zip, no live shared folder needed"


def add_args(p: argparse.ArgumentParser) -> None:
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--export", action="store_true", help="export the files beneath the path")
    g.add_argument("--import", dest="source", metavar="DIR_OR_ZIP", help="import files from a folder or .zip")
    p.add_argument("--media", action="store_true", help="export: also include assets/ and the edited video/subtitles")
    p.add_argument("--nested", action="store_true", help="export: use the pipeline's own U01/T01/topic.md layout, not the flat OneDrive names")
    p.add_argument("--dry-run", action="store_true", help="import: report what would happen, write nothing")


def _module_files(root: Path, module: str, unit: str | None) -> list[str]:
    """Files directly under a module (course-map.md, assignment-N.md, ...) and each
    in-scope unit's activity.md — found by walking, since which of these exist is
    optional. `unit` narrows to one unit's activity.md and drops the module-level files,
    matching how a unit- or topic-scoped export shouldn't pull in the whole module."""
    out: list[str] = []
    mdir = root / module
    if not mdir.is_dir():
        return out
    if unit is None:
        for f in sorted(mdir.glob("*.md")):
            if not is_noise(f.name):
                out.append(f.name)
    unit_dirs = [mdir / unit] if unit else sorted(mdir.glob("U[0-9][0-9]"))
    for unit_dir in unit_dirs:
        activity = unit_dir / "activity.md"
        if activity.is_file():
            out.append(f"{unit_dir.name}/activity.md")
    return out


def _export(env: Envelope, target: Target, media: bool, nested: bool) -> None:
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    scope = target.rel.replace("/", "-") if target.rel != "." else "programme"
    batch = f"{stamp}-{scope}"
    exports = target.root / "transfer" / "exports"
    n = 2
    while (exports / f"{batch}.zip").exists():
        batch = f"{stamp}-{scope}-{n}"
        n += 1

    # A unit- or topic-scoped export only pulls in that unit's activity.md, not the whole
    # module's course-map/assignments/other units (target.modules is always [module] for
    # any non-root scope, so the narrowing has to come from target.level/rel, not modules).
    unit = target.rel.split("/")[1] if target.level in ("unit", "topic") else None
    included: list[tuple[str, str]] = []
    for module in target.modules:
        included.extend((module, rel) for rel in _module_files(target.root, module, unit))
    for t in target.topics:
        for lang_file in (t.src("en"), t.src("zh")):
            if lang_file.is_file():
                included.append((t.module, f"{t.unit}/{t.code}/{lang_file.name}"))
        if media:
            if t.assets.is_dir():
                for f in sorted(t.assets.iterdir()):
                    if f.is_file() and not is_noise(f.name):
                        included.append((t.module, f"{t.unit}/{t.code}/assets/{f.name}"))
            for f in (t.video, t.srt("en"), t.srt("zh")):
                if f.is_file():
                    included.append((t.module, f"{t.unit}/{t.code}/edit/{f.name}"))

    if not included:
        env.diagnostics.append(Diagnostic("XFER_NOTHING_TO_EXPORT", "Nothing in scope matched a file bcn transfer recognises.",
                                          hint="Check the path: a topic needs a topic.md, a module needs a course-map.md, etc."))
        return

    work = Path(tempfile.mkdtemp(prefix=".transfer-export-", dir=target.root))
    try:
        manifest_files = []
        for module, local_rel in included:
            src = target.root / module / local_rel
            name = local_rel if nested else (local_to_remote(module, local_rel) or local_rel.replace("/", "-"))
            # Nested keeps a real module subfolder; flat names already carry the module as
            # a filename prefix via local_to_remote(), so no extra nesting is needed there.
            dest = work / module / name if nested else work / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            manifest_files.append({"module": module, "local": local_rel, "name": str(dest.relative_to(work)), "sha256": sha256_file(dest)})
        fsutil.write_json(work / "manifest.json", {"batch": batch, "created": utcnow(), "nested": nested, "media": media, "files": manifest_files})
        exports.mkdir(parents=True, exist_ok=True)
        zpath = exports / f"{batch}.zip"
        with fsutil.atomic_path(zpath) as tmp:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                for f in sorted(work.rglob("*")):
                    if f.is_file():
                        z.write(f, str(f.relative_to(work)))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    env.artifacts.append(env.artifact(zpath, "transfer-export"))
    env.extra["batch"] = batch
    env.extra["zip"] = env.rel(zpath)
    env.extra["file_count"] = len(included)
    r = TopicResult(target.rel, target.rel)
    r.ok = True
    r.extra["exported"] = len(included)
    env.results.append(r)


def _module_of(rel: str) -> tuple[str | None, str]:
    """Splits a zip entry into (module, path relative to the module), trying a real
    module subfolder first (nested exports), then a flat name's own KV7016- prefix."""
    parts = rel.split("/", 1)
    if len(parts) == 2 and len(parts[0]) == 6:
        return parts[0], parts[1]
    stem = Path(rel).name
    if len(stem) > 7 and stem[6] == "-":
        return stem[:6], rel
    return None, rel


def _in_scope(target: Target, module: str, local_rel: str) -> bool:
    """Whether a destination path falls under the target's scope — not just its module,
    since a unit- or topic-scoped import must not reach outside that unit/topic either."""
    if target.level == "root":
        return True
    if module not in target.modules:
        return False
    if target.level == "module":
        return True
    # unit/topic: target.rel is "MODULE/UNIT" or "MODULE/UNIT/TOPIC"; local_rel is
    # relative to the module, so it must start with the unit (and topic, if given).
    scope_within_module = "/".join(target.rel.split("/")[1:])
    return local_rel == scope_within_module or local_rel.startswith(scope_within_module + "/")


def _import(env: Envelope, target: Target, source: str, dry_run: bool) -> None:
    src = Path(source).expanduser()
    if not src.exists():
        raise Fail("FS_MISSING", f"{source} does not exist.")
    tmp = None
    touched_modules: set[str] = set()
    try:
        if src.is_file() and src.suffix.lower() == ".zip":
            tmp = Path(tempfile.mkdtemp(prefix=".transfer-import-", dir=target.root))
            with zipfile.ZipFile(src) as z:
                for member in z.namelist():
                    if member.endswith("/") or is_noise(Path(member).name) or "__MACOSX" in member:
                        continue
                    dest = tmp / member
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(z.read(member))
            base = tmp
        elif src.is_dir():
            base = src
        else:
            raise Fail("USAGE", f"{source} must be a folder or a .zip.")

        for f in sorted(base.rglob("*")):
            if not f.is_file() or is_noise(f.name) or "__MACOSX" in f.parts or f.name == "manifest.json":
                continue
            rel = str(f.relative_to(base))
            module, inner = _module_of(rel)
            local_rel = remote_to_local(module, inner) if module else None
            r = TopicResult(rel, rel)
            env.results.append(r)
            if not module or not local_rel:
                r.ok = False
                r.extra["action"] = "unrecognized"
                r.diagnostics.append(Diagnostic("XFER_UNRECOGNIZED", f"'{rel}' does not match a known file name; it was not placed.",
                                                file=rel, hint="Check the file name against bcn sync's naming (e.g. KV7016-U01-T01.md, KV7016-course-map.md)."))
                continue
            if not _in_scope(target, module, local_rel):
                r.ok = False
                r.extra["action"] = "refused"
                r.diagnostics.append(Diagnostic("XFER_OUT_OF_SCOPE", f"{module}/{local_rel} is outside {target.rel}, where this import was aimed.", file=rel))
                continue
            dest = target.root / module / local_rel
            r.extra["path"] = f"{module}/{local_rel}"
            data = f.read_bytes()
            if dest.is_file():
                current = dest.read_bytes()
                if current == data:
                    r.extra["action"] = "unchanged"
                    r.diagnostics.append(Diagnostic("XFER_UNCHANGED", f"{module}/{local_rel} already holds this content.", file=rel))
                    touched_modules.add(module)
                    continue
                diff = ""
                try:
                    diff = "".join(difflib.unified_diff(current.decode("utf-8").splitlines(True), data.decode("utf-8").splitlines(True),
                                                        fromfile=f"{module}/{local_rel} (on disk)", tofile=rel))
                except UnicodeDecodeError:
                    pass  # binary (media): report the conflict without a text diff
                r.ok = False
                r.extra["action"] = "exists_differs"
                r.extra["diff"] = diff
                r.diagnostics.append(Diagnostic("XFER_EXISTS_DIFFERS", f"{module}/{local_rel} exists and differs; nothing was written.",
                                                file=rel, data={"diff": diff} if diff else None,
                                                hint="Review the diff. If the import is right, edit or remove the file on disk yourself."))
                continue
            if dry_run:
                r.extra["action"] = "would_write"
            else:
                fsutil.write_bytes(dest, data)
                r.extra["action"] = "written"
                r.diagnostics.append(Diagnostic("XFER_WRITTEN", f"Wrote {module}/{local_rel}.", file=rel))
            touched_modules.add(module)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    if touched_modules and not dry_run:
        all_topics = []
        for module in sorted(touched_modules):
            try:
                all_topics.extend(resolve_target(str(target.root / module)).topics)
            except Fail:
                continue
        if all_topics:
            sub = Target(target.root, target.path, target.level, target.rel, sorted(touched_modules), all_topics, [])
            step_env = Envelope("validate", target.rel, target.root)
            run_topics(step_env, sub, "validate", "en", lambda t, r, tp, cfg: validate_cmd.check_topic(t, r, tp, cfg, "en"))
            env.extra["validated"] = [{"topic": sr.topic, "ok": sr.ok} for sr in step_env.results]


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    if args.export:
        _export(env, target, args.media, args.nested)
    else:
        _import(env, target, args.source, args.dry_run)
