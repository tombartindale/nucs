"""bcn bundle: carry one job's inputs to a remote runner, and its outputs back.

  bcn bundle <path> create --archive FILE --cmd CMD
        Snapshot what CMD needs for the topic(s) under <path> into a tar.gz laid out like the
        programme root (programme.toml, themes/, each module's module.toml and course-map.md,
        each topic's own files). File mtimes are kept, so the freshness checks on the runner
        behave exactly as they would here. The snapshot's stamp (the time it started) is
        returned in the envelope; apply needs it back.
  bcn bundle <path> collect --archive FILE
        Runner side, inside an extracted snapshot: archive the files CMD is allowed to write
        that changed during the run, plus the allowed files it removed.
  bcn bundle <path> apply --archive FILE --cmd CMD --stamp T
        Write a runner's output archive into this tree. Only CMD's own outputs under the
        target's topics are accepted (REMOTE_SPECS); anything else rejects the whole archive
        and nothing is written. Written files are stamped T, the snapshot time, so an input
        edited while the runner worked is newer than the result and shows as stale.
  bcn bundle <path> specs
        List the commands that can run remotely.

Video never travels in an archive: a command that needs it (compose) reads and publishes
through object storage (bcn/storage.py), so remote compose requires a bucket.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tarfile
import time
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from .. import __version__, fsutil, storage
from ..config import load
from ..envelope import Envelope, Fail
from ..tree import Target, Topic, is_noise

HELP = "carry a job's inputs to a remote runner and its outputs back"

SNAPSHOT_FILE = ".bcn-snapshot.json"
OUTPUTS_FILE = ".bcn-outputs.json"
MAX_MEMBER_BYTES = 512 << 20
MAX_TOTAL_BYTES = 2 << 30


@dataclass(frozen=True)
class RemoteSpec:
    outputs: tuple[str, ...]  # globs relative to the topic directory, matched segment by segment
    needs_storage: bool = False  # reads or writes video, which only travels through the bucket
    needs_master: bool = False


REMOTE_SPECS: dict[str, RemoteSpec] = {
    "compose": RemoteSpec(
        ("build/compose.json", "build/compose.*.json", "build/composed*.mp4.s3.json", "build/draft*.mp4.s3.json",
         "build/subtitles/*.delivery.srt"),
        needs_storage=True, needs_master=True),
    "bumpers": RemoteSpec(
        ("build/bumpers.json", "build/bumpers.*.json", "build/bumpers/*.mp4", "build/bumpers/*.png")),
    "render": RemoteSpec(
        ("build/render.json", "build/render.*.json", "build/slides/*/*.png", "build/deck.*.pdf", "build/deck.*.html")),
}


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("action", choices=["create", "collect", "apply", "specs"])
    p.add_argument("--archive", default=None, help="the tar.gz to write (create, collect) or read (apply)")
    p.add_argument("--cmd", default=None, help="create, apply: the command the runner runs")
    p.add_argument("--stamp", type=float, default=None, help="apply: the stamp create returned")


def _spec(cmd: str | None) -> RemoteSpec:
    if not cmd or cmd not in REMOTE_SPECS:
        raise Fail("USAGE", f"'{cmd}' cannot run remotely. Choose from {', '.join(sorted(REMOTE_SPECS))}.")
    return REMOTE_SPECS[cmd]


def _matches(inner: str, spec: RemoteSpec) -> bool:
    """fnmatch, but '*' never crosses a '/'."""
    parts = inner.split("/")
    for pat in spec.outputs:
        pp = pat.split("/")
        if len(pp) == len(parts) and all(fnmatchcase(a, b) for a, b in zip(parts, pp)):
            return True
    return False


def _walk(d: Path, keep) -> list[Path]:
    out: list[Path] = []
    if not d.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(d):
        base = Path(dirpath)
        dirnames[:] = sorted(n for n in dirnames if not n.startswith(".") and not is_noise(n)
                             and not (base / n).is_symlink() and keep(base / n, True))
        out.extend(base / n for n in sorted(filenames)
                   if not is_noise(n) and not (base / n).is_symlink() and keep(base / n, False))
    return out


def _topic_keep(t: Topic):
    def keep(p: Path, is_dir: bool) -> bool:
        rel = p.relative_to(t.dir).parts
        if rel[0] == "out" or p.name.startswith("."):
            return False  # deliveries, .history, scratch and partial files
        # Video stays in the bucket; only the small bumper clips compose overlays travel.
        return is_dir or p.suffix != ".mp4" or rel[:2] == ("build", "bumpers")
    return keep


def snapshot_files(target: Target) -> list[Path]:
    root = target.root
    files = [root / "programme.toml", *_walk(root / "themes", lambda p, d: True)]
    for m in sorted({t.module for t in target.topics}):
        files += [p for p in (root / m / "module.toml", root / m / "course-map.md") if p.is_file()]
        cfg = load(root, root / m)
        for kind in ("intro", "outro"):
            rel = cfg["bumpers"][kind]
            p = (root / rel).resolve() if rel else None
            if p and p.is_file() and p.is_relative_to(root.resolve()):
                files.append(root / p.relative_to(root.resolve()))
    for t in target.topics:
        files += _walk(t.dir, _topic_keep(t))
    return list(dict.fromkeys(files))


def create(env: Envelope, target: Target, cmd: str | None, archive: Path) -> None:
    stamp = time.time()  # before any file is read: an edit made during the snapshot counts as later
    spec = _spec(cmd)
    root = target.root
    topics = [t for t in target.topics if t.dir.is_dir()]
    if not topics:
        raise Fail("USAGE", f"{target.rel} has no topics to send.")
    if spec.needs_storage and storage.remote(root) is None:
        raise Fail("CONFIG_INVALID", f"{cmd} can only run remotely when video lives in object storage.",
                   hint="Set S3_MEDIA_BUCKET (or [storage] bucket) and run bcn media migrate.")
    if spec.needs_master:
        local = [t for t in topics if t.video.is_file()]
        if local:
            raise Fail("BUNDLE_SNAPSHOT", f"{local[0].rel}/edit/master.mp4 is on local disk, so a runner cannot reach it.",
                       topic=local[0].id, file="edit/master.mp4",
                       hint=f"bcn media {local[0].rel} migrate --apply --delete")
    files = snapshot_files(target)
    manifest = {
        "stamp": stamp, "cmd": cmd, "target": target.rel, "topics": [t.rel for t in topics],
        "bcn_version": __version__,
        "files": {f.relative_to(root).as_posix(): [f.stat().st_mtime, f.stat().st_size] for f in files},
    }
    archive.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with fsutil.atomic_path(archive, suffix=".partial.tar.gz") as tmp:
        with tarfile.open(tmp, "w:gz", format=tarfile.PAX_FORMAT, compresslevel=1) as tf:
            _add_json(tf, SNAPSHOT_FILE, manifest)
            for f in files:
                tf.add(f, arcname=f.relative_to(root).as_posix(), recursive=False)
                total += f.stat().st_size
    env.extra.update({"archive": str(archive), "stamp": stamp, "files": len(files), "bytes": total})


def _add_json(tf: tarfile.TarFile, name: str, data: object) -> None:
    import io
    raw = json.dumps(data, ensure_ascii=False).encode()
    info = tarfile.TarInfo(name)
    info.size, info.mtime, info.mode = len(raw), time.time(), 0o644
    tf.addfile(info, io.BytesIO(raw))


def collect(env: Envelope, target: Target, archive: Path) -> None:
    root = target.root
    snap = fsutil.read_json(root / SNAPSHOT_FILE)
    if not isinstance(snap, dict):
        raise Fail("BUNDLE_SNAPSHOT", f"{root} is not an extracted bundle (no {SNAPSHOT_FILE}).")
    spec = _spec(snap.get("cmd"))
    before: dict[str, list[float]] = snap.get("files", {})
    topics = [t for t in target.topics if t.rel in set(snap.get("topics", []))]
    changed: list[Path] = []
    deleted: list[str] = []
    for t in topics:
        for f in _walk(t.dir, lambda p, d: not p.name.startswith(".")):
            inner = f.relative_to(t.dir).as_posix()
            rel = f.relative_to(root).as_posix()
            if _matches(inner, spec) and before.get(rel) != [f.stat().st_mtime, f.stat().st_size]:
                changed.append(f)
        prefix = t.rel + "/"
        deleted += [rel for rel in before if rel.startswith(prefix) and _matches(rel[len(prefix):], spec)
                    and not (root / rel).exists()]
    with fsutil.atomic_path(archive, suffix=".partial.tar.gz") as tmp:
        with tarfile.open(tmp, "w:gz", format=tarfile.PAX_FORMAT, compresslevel=1) as tf:
            _add_json(tf, OUTPUTS_FILE, {"cmd": snap.get("cmd"), "deleted": deleted})
            for f in changed:
                tf.add(f, arcname=f.relative_to(root).as_posix(), recursive=False)
    env.extra.update({"archive": str(archive), "written": [f.relative_to(root).as_posix() for f in changed],
                      "deleted": deleted})


def _check(rel: str, topics: set[str], spec: RemoteSpec, root: Path) -> str | None:
    """Why rel may not be written, or None when it may."""
    if "\\" in rel or rel.startswith("/"):
        return "is not a relative path"
    parts = PurePosixPath(rel).parts
    if any(p in ("..", ".", "") for p in parts) or len(parts) < 4:
        return "is not a path inside a topic"
    if "/".join(parts[:3]) not in topics:
        return "is outside the job's topics"
    if not _matches("/".join(parts[3:]), spec):
        return "is not an output of this command"
    if (root / rel).is_symlink():
        return "would replace a symlink"
    return None


def apply(env: Envelope, target: Target, cmd: str | None, archive: Path, stamp: float | None) -> None:
    spec = _spec(cmd)
    if stamp is None:
        raise Fail("USAGE", "apply needs --stamp, the stamp create returned.")
    if not archive.is_file():
        raise Fail("FS_MISSING", f"{archive} does not exist.")
    root = target.root
    topics = {t.rel for t in target.topics}
    store = storage.remote(root)
    writes: list[tuple[str, tarfile.TarInfo]] = []
    deleted: list[str] = []
    total = 0

    def reject(name: str, why: str) -> None:
        raise Fail("BUNDLE_REJECTED", f"The runner's output '{name}' {why}; nothing was applied.", file=name,
                   hint="The runner is running a different bcn, or something other than bcn wrote its outputs.")

    with tarfile.open(archive, "r:gz") as tf:
        # Pass 1: check every member before anything is written.
        for m in tf.getmembers():
            if m.name == OUTPUTS_FILE and m.isreg():
                meta = json.loads(tf.extractfile(m).read())  # type: ignore[union-attr]
                for rel in meta.get("deleted", []):
                    if why := _check(str(rel), topics, spec, root):
                        reject(str(rel), why)
                    deleted.append(str(rel))
                continue
            if m.isdir():
                continue
            if not m.isreg():
                reject(m.name, "is not a regular file")
            if why := _check(m.name, topics, spec, root):
                reject(m.name, why)
            total += m.size
            if m.size > MAX_MEMBER_BYTES or total > MAX_TOTAL_BYTES:
                reject(m.name, "is larger than any output this command makes")
            if m.name.endswith(storage.POINTER_SUFFIX):
                ptr = json.loads(tf.extractfile(m).read() or b"null")  # type: ignore[union-attr]
                media = root / m.name[: -len(storage.POINTER_SUFFIX)]
                if store is None or not isinstance(ptr, dict) or ptr.get("key") != store.key(media):
                    reject(m.name, "points somewhere other than its own object in the bucket")
            writes.append((m.name, m))
        # Pass 2: write. Everything carries the snapshot's stamp (see the module docstring).
        for rel, m in writes:
            dest = root / rel
            with fsutil.atomic_path(dest) as tmp:
                with tf.extractfile(m) as src, open(tmp, "wb") as out:  # type: ignore[union-attr]
                    shutil.copyfileobj(src, out)
            os.utime(dest, (stamp, stamp))
    gone = []
    for rel in deleted:
        p = root / rel
        m = fsutil.mtime(p) if p.is_file() else None
        if m is not None and m <= stamp:  # never remove something replaced here since the snapshot
            p.unlink()
            gone.append(rel)
    env.extra.update({"stamp": stamp, "written": [rel for rel, _ in writes], "deleted": gone})


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    if args.action == "specs":
        env.extra["remote_commands"] = sorted(REMOTE_SPECS)
        return
    if not args.archive:
        raise Fail("USAGE", f"{args.action} needs --archive.")
    archive = Path(args.archive).resolve()
    if args.action == "create":
        create(env, target, args.cmd, archive)
    elif args.action == "collect":
        collect(env, target, archive)
    else:
        apply(env, target, args.cmd, archive, args.stamp)
