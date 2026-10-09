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
          not guessed at. By default a file that already exists with different
          content is never overwritten: the diff comes back instead. --replace
          overwrites it anyway, for re-importing corrected content.
--full    Root-scope only. Everything --media already covers, plus programme.toml
          and themes/custom/ (if present) at the zip's top level, so the result is
          enough to rebuild the whole programme on a fresh server: a disaster-
          recovery backup, not just a content handover.

This exists for the same handover bcn sync used to do against a live OneDrive
folder, but as a one-time transfer with no persistent synced state: a content
creator or partner gets or sends one zip, not a live two-way folder.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import filecmp
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from .. import fsutil
from ..envelope import Cancelled, Diagnostic, Envelope, Fail, TopicResult, sha256_file, utcnow
from ..progress import CANCEL, Progress
from ..runner import run_topics
from ..sync import local_to_remote, remote_to_local
from ..tree import MODULE_RE, Target, is_noise
from ..tree import resolve as resolve_target
from . import validate as validate_cmd

HELP = "export or import a whole module/unit as a single zip, no live shared folder needed"

# --full's two extras, read/written at the zip's top level rather than under a module —
# never collide with local_to_remote()'s module-prefixed flat names (those are always
# "XX0000-..."), or with a real nested module subfolder (always exactly 6 characters).
FULL_PROGRAMME_TOML = "programme.toml"
FULL_THEME_PREFIX = "themes/custom/"
# --full's pipeline state: human decisions (review, translation), sync's baseline, and the
# step results / manifests that say what has already been built. Stored under state/ so the
# zip path is always "state/<module>/..." and import can accept exactly these paths, nothing else.
FULL_STATE_PREFIX = "state/"
FULL_STATE_ROOT = "sync-state.json"
STATE_PATH_RE = re.compile(r"[A-Z]{2}\d{4}/U\d{2}/T\d{2}/(?:review\.json|translation\.json|(?:build|out)/[^/]+\.(?:json|csv))")


def add_args(p: argparse.ArgumentParser) -> None:
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--export", action="store_true", help="export the files beneath the path")
    g.add_argument("--import", dest="source", metavar="DIR_OR_ZIP_OR_MEDIA", help="import files from a folder, a .zip, or a single .mp4/.srt")
    p.add_argument("--media", action="store_true", help="export: also include assets/ and the edited video/subtitles")
    p.add_argument("--nested", action="store_true", help="export: use the pipeline's own U01/T01/topic.md layout, not the flat OneDrive names")
    p.add_argument("--full", action="store_true",
                   help="root scope only: a disaster-recovery backup, not just content -- implies --media, and also "
                        "carries programme.toml and themes/custom/")
    p.add_argument("--dry-run", action="store_true", help="import: report what would happen, write nothing")
    p.add_argument("--replace", action="store_true",
                   help="import: overwrite a file that already exists and differs, instead of refusing")
    p.add_argument("--init", action="store_true",
                   help="import --full: create the root (with a placeholder programme.toml) if it does not exist yet, "
                        "so a disaster-recovery restore has a root to resolve before --full's own programme.toml overwrites it")


# prepare() sets this on args when it actually bootstraps a placeholder root, so _import
# (same process, same args object) knows to let --full's own programme.toml overwrite that
# placeholder unconditionally rather than treating it as a real, protect-worthy conflict.
BOOTSTRAPPED_ATTR = "_transfer_bootstrapped"


def prepare(args: argparse.Namespace) -> None:
    """Runs before the path is resolved, so --init can create the root for a from-scratch
    --full restore — resolve()/find_root() need a programme.toml to find the root at all,
    the same bootstrap problem bcn sync --init solves for its own first-time case."""
    setattr(args, BOOTSTRAPPED_ATTR, False)
    if not args.init:
        return
    if not args.full or args.export:
        raise Fail("USAGE", "--init only makes sense with --import --full.")
    root = Path(args.path).expanduser()
    if (root / "programme.toml").is_file():
        return
    if root.exists() and any(root.iterdir()):
        raise Fail("USAGE", f"{root} exists and is not empty; --init only creates a new, empty programme root.")
    root.mkdir(parents=True, exist_ok=True)
    # Placeholder only: --full's own programme.toml in the zip overwrites this immediately
    # (it is read before any file is compared, so nothing else looks at it in between).
    (root / "programme.toml").write_text("[programme]\nname = \"\"\n", encoding="utf-8")
    setattr(args, BOOTSTRAPPED_ATTR, True)


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


def _export(env: Envelope, target: Target, media: bool, nested: bool, full: bool) -> None:
    if full and target.level != "root":
        raise Fail("USAGE", "--full only makes sense for the whole programme.", hint="Run it against the programme root, not a module/unit/topic path.")
    media = media or full
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    scope = "programme-full" if full else (target.rel.replace("/", "-") if target.rel != "." else "programme")
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

    # (source path, name inside the zip, manifest module-or-None) for every file, content
    # and extras together, so one progress-reported loop below writes and hashes them all.
    entries: list[tuple[Path, str, str | None]] = []
    for module, local_rel in included:
        src = target.root / module / local_rel
        name = local_rel if nested else (local_to_remote(module, local_rel) or local_rel.replace("/", "-"))
        # Nested keeps a real module subfolder; flat names already carry the module as a
        # filename prefix via local_to_remote(), so no extra nesting is needed there.
        entries.append((src, f"{module}/{name}" if nested else name, module))
    if full:
        toml = target.root / FULL_PROGRAMME_TOML
        if toml.is_file():
            entries.append((toml, FULL_PROGRAMME_TOML, None))
        sync_state = target.root / FULL_STATE_ROOT
        if sync_state.is_file():
            entries.append((sync_state, FULL_STATE_PREFIX + FULL_STATE_ROOT, None))
        for t in target.topics:
            for f in [t.review_file, t.translation_file, *sorted(t.build.glob("*.json")), *sorted(t.build.glob("*.csv")), *sorted(t.out.glob("*.json"))]:
                if f.is_file():
                    entries.append((f, FULL_STATE_PREFIX + f.relative_to(target.root).as_posix(), t.module))
        theme_dir = target.root / "themes" / "custom"
        if theme_dir.is_dir():
            for f in sorted(theme_dir.rglob("*")):
                if f.is_file() and not is_noise(f.name):
                    entries.append((f, f"{FULL_THEME_PREFIX}{f.relative_to(theme_dir)}", None))

    if not entries:
        env.diagnostics.append(Diagnostic("XFER_NOTHING_TO_EXPORT", "Nothing in scope matched a file bcn transfer recognises.",
                                          hint="Check the path: a topic needs a topic.md, a module needs a course-map.md, etc."))
        return

    exports.mkdir(parents=True, exist_ok=True)
    zpath = exports / f"{batch}.zip"
    manifest_files: list[dict] = []
    try:
        with fsutil.atomic_path(zpath) as tmp:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z, Progress("transfer", len(entries)) as prog:
                for src, name, module in entries:
                    if CANCEL.is_set():
                        prog.done(cancelled=True)
                        raise Cancelled()
                    with prog.topic(name) as tp:
                        tp.update(0, f"zipping {name}")
                        z.write(src, name)
                        manifest_files.append({"module": module, "name": name, "sha256": sha256_file(src)})
                        tp.update(100, "zipped")
                prog.done()
                manifest = {"batch": batch, "created": utcnow(), "nested": nested, "media": media, "full": full, "files": manifest_files}
                z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    except Cancelled:
        env.cancelled = True
        return

    env.artifacts.append(env.artifact(zpath, "transfer-export"))
    env.extra["batch"] = batch
    env.extra["zip"] = env.rel(zpath)
    env.extra["file_count"] = len(entries)
    r = TopicResult(target.rel, target.rel)
    r.ok = True
    r.extra["exported"] = len(entries)
    env.results.append(r)


def _module_of(rel: str) -> tuple[str | None, str]:
    """Splits a zip entry into (module, path relative to the module), trying a real
    module subfolder first (nested exports), then a flat name's own KV7016- prefix.
    The first branch checks the folder name is actually a module code (KV7015, not just
    any 6-character name — a plain top-level "assets" folder is also 6 characters and
    would otherwise be misread as one)."""
    parts = rel.split("/", 1)
    if len(parts) == 2 and MODULE_RE.fullmatch(parts[0]):
        return parts[0], parts[1]
    stem = Path(rel).name
    if len(stem) > 7 and stem[6] == "-" and MODULE_RE.fullmatch(stem[:6]):
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


# A diff is only worth reading both files fully for, and only means anything for text:
# above this a conflict is just reported without one, matching how a binary (media) file
# already skipped the diff via UnicodeDecodeError.
DIFF_MAX_BYTES = 2 * 1024 * 1024


def _place(env: Envelope, rel: str, f: Path, dest: str, dry_run: bool, overwrite: bool = False) -> tuple[str, bool]:
    """Compares f (a staged import file) against the real path dest, streaming rather
    than reading either fully into memory — the file may be a multi-GB video. Returns
    (action, touched) for the caller's TopicResult/diagnostic and touched-module bookkeeping.
    overwrite skips the diff-protection entirely: only for the placeholder programme.toml
    a --full --init bootstrap just wrote, which is not real content worth protecting."""
    dest_path = Path(dest)
    r = TopicResult(rel, rel)
    env.results.append(r)
    r.extra["path"] = dest
    if dest_path.is_file() and not overwrite:
        if filecmp.cmp(f, dest_path, shallow=False):
            r.extra["action"] = "unchanged"
            r.diagnostics.append(Diagnostic("XFER_UNCHANGED", f"{dest} already holds this content.", file=rel))
            return "unchanged", True
        diff = ""
        if f.stat().st_size <= DIFF_MAX_BYTES and dest_path.stat().st_size <= DIFF_MAX_BYTES:
            try:
                diff = "".join(difflib.unified_diff(dest_path.read_text("utf-8").splitlines(True), f.read_text("utf-8").splitlines(True),
                                                    fromfile=f"{dest} (on disk)", tofile=rel))
            except UnicodeDecodeError:
                pass  # binary (media): report the conflict without a text diff
        r.ok = False
        r.extra["action"] = "exists_differs"
        r.extra["diff"] = diff
        r.diagnostics.append(Diagnostic("XFER_EXISTS_DIFFERS", f"{dest} exists and differs; nothing was written.",
                                        file=rel, data={"diff": diff} if diff else None,
                                        hint="Review the diff. If the import is right, edit or remove the file on disk yourself."))
        return "exists_differs", False
    if dry_run:
        r.extra["action"] = "would_write"
    else:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with fsutil.atomic_path(dest_path) as tmp, open(f, "rb") as src_f, open(tmp, "wb") as out_f:
            shutil.copyfileobj(src_f, out_f)
        r.extra["action"] = "written"
        r.diagnostics.append(Diagnostic("XFER_WRITTEN", f"Wrote {dest}.", file=rel))
    return r.extra["action"], True


MEDIA_SUFFIXES = (".mp4", ".srt")


def _import(env: Envelope, target: Target, source: str, dry_run: bool, full: bool, bootstrapped: bool = False, replace: bool = False) -> None:
    src = Path(source).expanduser()
    if not src.exists():
        raise Fail("FS_MISSING", f"{source} does not exist.")
    tmp = None
    touched_modules: set[str] = set()
    single = src if src.is_file() and src.suffix.lower() in MEDIA_SUFFIXES else None
    try:
        if single is not None:
            base = src.parent
        elif src.is_file() and src.suffix.lower() == ".zip":
            tmp = Path(tempfile.mkdtemp(prefix=".transfer-import-", dir=target.root))
            with zipfile.ZipFile(src) as z:
                members = [m for m in z.namelist() if not m.endswith("/") and not is_noise(Path(m).name) and "__MACOSX" not in m]
                with Progress("transfer-extract", len(members)) as prog:
                    for member in members:
                        if CANCEL.is_set():
                            prog.done(cancelled=True)
                            env.cancelled = True
                            return
                        with prog.topic(member) as tp:
                            tp.update(0, f"extracting {member}")
                            dest = tmp / member
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            with z.open(member) as zf, open(dest, "wb") as out:
                                shutil.copyfileobj(zf, out)
                            tp.update(100, "extracted")
                    prog.done()
            base = tmp
        elif src.is_dir():
            base = src
        else:
            raise Fail("USAGE", f"{source} must be a folder, a .zip, or a single .mp4/.srt file.")

        if single is not None:
            files = [single]
        else:
            files = [f for f in sorted(base.rglob("*")) if f.is_file() and not is_noise(f.name) and "__MACOSX" not in f.parts and f.name != "manifest.json"]
        with Progress("transfer", len(files)) as prog:
            for f in files:
                if CANCEL.is_set():
                    prog.done(cancelled=True)
                    env.cancelled = True
                    return
                rel = str(f.relative_to(base))
                with prog.topic(rel) as tp:
                    tp.update(0, f"placing {rel}")
                    if rel.startswith(FULL_STATE_PREFIX):
                        inner = rel[len(FULL_STATE_PREFIX):]
                        r = TopicResult(rel, rel)
                        if not full:
                            r.ok = False
                            r.extra["action"] = "refused"
                            r.diagnostics.append(Diagnostic("XFER_OUT_OF_SCOPE", f"{rel} is --full pipeline state; pass --full to import it.", file=rel))
                            env.results.append(r)
                        elif inner == FULL_STATE_ROOT:
                            _place(env, rel, f, str(target.root / inner), dry_run)
                        elif STATE_PATH_RE.fullmatch(inner):
                            module, local = inner.split("/", 1)
                            if not _in_scope(target, module, local):
                                r.ok = False
                                r.extra["action"] = "refused"
                                r.diagnostics.append(Diagnostic("XFER_OUT_OF_SCOPE", f"{inner} is outside {target.rel}, where this import was aimed.", file=rel))
                                env.results.append(r)
                            else:
                                action, touched = _place(env, rel, f, str(target.root / inner), dry_run)
                                if touched:
                                    touched_modules.add(module)
                        else:
                            r.ok = False
                            r.extra["action"] = "unrecognized"
                            r.diagnostics.append(Diagnostic("XFER_UNRECOGNIZED", f"'{rel}' is not a known pipeline state file; it was not placed.", file=rel))
                            env.results.append(r)
                        tp.update(100, "placed")
                        continue
                    if rel == FULL_PROGRAMME_TOML or rel.startswith(FULL_THEME_PREFIX):
                        if not full:
                            r = TopicResult(rel, rel)
                            r.ok = False
                            r.extra["action"] = "refused"
                            r.diagnostics.append(Diagnostic("XFER_OUT_OF_SCOPE", f"{rel} is a --full extra; pass --full to import it.", file=rel))
                            env.results.append(r)
                        else:
                            overwrite = bootstrapped and rel == FULL_PROGRAMME_TOML
                            _place(env, rel, f, str(target.root / rel), dry_run, overwrite=overwrite)
                        tp.update(100, "placed")
                        continue
                    module, inner = _module_of(rel)
                    local_rel = remote_to_local(module, inner) if module else None
                    if not module or not local_rel:
                        r = TopicResult(rel, rel)
                        r.ok = False
                        r.extra["action"] = "unrecognized"
                        r.diagnostics.append(Diagnostic("XFER_UNRECOGNIZED", f"'{rel}' does not match a known file name; it was not placed.",
                                                        file=rel, hint="Check the file name against bcn sync's naming (e.g. KV7016-U01-T01.md, KV7016-course-map.md)."))
                        env.results.append(r)
                        tp.update(100, "unrecognized")
                        continue
                    if not _in_scope(target, module, local_rel):
                        r = TopicResult(rel, rel)
                        r.ok = False
                        r.extra["action"] = "refused"
                        r.diagnostics.append(Diagnostic("XFER_OUT_OF_SCOPE", f"{module}/{local_rel} is outside {target.rel}, where this import was aimed.", file=rel))
                        env.results.append(r)
                        tp.update(100, "refused")
                        continue
                    action, touched = _place(env, rel, f, str(target.root / module / local_rel), dry_run, overwrite=replace)
                    if touched:
                        touched_modules.add(module)
                    tp.update(100, action)
            prog.done()
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
        _export(env, target, args.media, args.nested, args.full)
    else:
        if args.full and target.level != "root":
            raise Fail("USAGE", "--full only makes sense for the whole programme.", hint="Run it against the programme root, not a module/unit/topic path.")
        # A root with no module folders has no real configuration to protect (it is the shipped
        # placeholder, or one --init just made), so a --full restore may replace its programme.toml.
        empty_root = args.full and not target.modules
        _import(env, target, args.source, args.dry_run, args.full,
                bootstrapped=empty_root or getattr(args, BOOTSTRAPPED_ATTR, False), replace=args.replace)
