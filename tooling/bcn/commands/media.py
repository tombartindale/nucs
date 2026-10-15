"""bcn media: move video between local disk and object storage, and tidy up after it.

  bcn media <path> migrate [--apply] [--delete]
        Upload every real media file under the topic(s) (edit/master.mp4, build/composed*.mp4,
        build/draft*.mp4, out/*.mp4) to the bucket and leave a pointer file in its place.
        Without --apply it only reports what it would do. --delete removes each local file
        once the upload has verified (size match), one file at a time, so disk frees as it goes.
  bcn media <topic> adopt --upload-key KEY [--replace]
        Take a video the browser uploaded straight to the bucket's staging area, check it,
        and make it the topic's master.mp4. Refuses to replace an existing master without --replace.
  bcn media <path> gc [--apply] [--hours N]
        Remove leftovers that only waste disk: incoming media uploads, aborted import and
        compose scratch directories older than N hours (default 24).

Storage is configured in programme.toml [storage] or S3_MEDIA_BUCKET (see bcn/storage.py).
"""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

from .. import fsutil, storage, tools
from ..config import Config
from ..envelope import Diagnostic, Envelope, Fail, TopicResult
from ..media import probe
from ..progress import TopicProgress
from ..runner import run_topics
from ..tree import Target, Topic

HELP = "move video to or from object storage, and clean up leftovers"


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("action", choices=["migrate", "adopt", "gc"])
    p.add_argument("--apply", action="store_true", help="do it (migrate and gc only report without this)")
    p.add_argument("--delete", action="store_true", help="migrate: remove each local file after its upload verifies")
    p.add_argument("--upload-key", default=None, help="adopt: the staging object the browser uploaded")
    p.add_argument("--replace", action="store_true", help="adopt: replace an existing master.mp4")
    p.add_argument("--hours", type=float, default=24.0, help="gc: only remove leftovers older than this")


def media_files(t: Topic) -> list[Path]:
    """Real (local) media files belonging to a topic, largest first."""
    found: list[Path] = []
    for d, pat in ((t.dir / "edit", "master.mp4"), (t.build, "composed*.mp4"), (t.build, "draft*.mp4"), (t.out, "*.mp4")):
        if d.is_dir():
            found.extend(p for p in d.glob(pat) if p.is_file() and not p.name.startswith("."))
    return sorted(set(found), key=lambda p: p.stat().st_size, reverse=True)


def _duration(cfg: Config, p: Path) -> float | None:
    try:
        return probe(tools.require(cfg, "ffprobe"), p).duration or None
    except Fail:
        return None


def migrate_topic(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config, apply: bool, delete: bool) -> None:
    store = storage.remote(t.root)
    if store is None:
        raise Fail("CONFIG_INVALID", "Object storage is not configured.",
                   hint="Set S3_MEDIA_BUCKET or [storage] bucket in programme.toml.")
    files = media_files(t)
    moved, freed = [], 0
    for i, p in enumerate(files):
        tp.update(100 * i / max(len(files), 1), f"{p.name}")
        size = p.stat().st_size
        rel = store.rel(p)
        if not apply:
            moved.append({"file": rel, "bytes": size, "action": "would_upload"})
            continue
        st = p.stat()
        store.put(p, p, dur=_duration(cfg, p), keep_mtime=st.st_mtime, delete_local=delete)
        freed += size if delete else 0
        moved.append({"file": rel, "bytes": size, "action": "uploaded_and_removed" if delete else "uploaded"})
    r.extra.update({"files": moved, "bytes": sum(m["bytes"] for m in moved), "freed": freed, "applied": apply})
    tp.update(100, "done")


def adopt_topic(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config, key: str, replace: bool) -> None:
    store = storage.remote(t.root)
    if store is None:
        raise Fail("CONFIG_INVALID", "Object storage is not configured.")
    dest = t.video
    if storage.present(dest) and not replace:
        raise Fail("XFER_EXISTS_DIFFERS", "edit/master.mp4 already exists; nothing was replaced.", file="edit/master.mp4",
                   hint="Upload again and confirm the replacement.")
    root = f"{store.prefix.strip('/')}/_incoming/"
    if not key.startswith(root) or ".." in key:
        raise Fail("USAGE", "That is not an upload staging key.", file=key)
    tp.update(10, "checking upload")
    try:
        head = store.client.head_object(Bucket=store.bucket, Key=key)
    except Exception as e:  # noqa: BLE001 - botocore ClientError, but boto3 is optional
        raise Fail("FS_MISSING", "The uploaded video is not in the bucket.", file=key) from e
    if int(head["ContentLength"]) == 0:
        raise Fail("FS_EMPTY", "The uploaded video is empty.", file="edit/master.mp4")
    if store.read_range(key, 4, 7) != b"ftyp":
        store.client.delete_object(Bucket=store.bucket, Key=key)
        raise Fail("FS_CORRUPT", "That file is not an MP4 video.", file="edit/master.mp4")
    tp.update(30, "reading duration")
    dur = None
    try:
        dur = probe(tools.require(cfg, "ffprobe"), store.presign_key(key), "edit/master.mp4").duration  # type: ignore[arg-type]
    except Fail:
        pass  # this ffprobe cannot read over https; the first command that needs it will fetch and probe
    if dur is not None and dur <= 0:
        store.client.delete_object(Bucket=store.bucket, Key=key)
        raise Fail("FS_CORRUPT", "edit/master.mp4 reports no duration.", file="edit/master.mp4")
    tp.update(70, "placing video")
    ptr = store.adopt_incoming(key, dest, dur=dur or None)
    r.extra.update({"path": "edit/master.mp4", "bytes": ptr["size"], "duration": ptr.get("duration"), "action": "adopted"})
    r.diagnostics.append(Diagnostic("XFER_WRITTEN", f"Wrote {store.key(dest)}.", file="edit/master.mp4"))
    tp.update(100, "done")


def _old(p: Path, hours: float) -> bool:
    m = fsutil.mtime(p)
    return m is not None and (time.time() - m) > hours * 3600


def gc(env: Envelope, target: Target, apply: bool, hours: float) -> None:
    root = target.root
    victims: list[Path] = []
    incoming = root / "transfer" / "incoming"
    if incoming.is_dir():
        victims += [p for p in incoming.iterdir() if p.name.startswith("media-") and _old(p, hours)]
    victims += [p for p in root.glob(".transfer-import-*") if _old(p, hours)]
    for t in target.topics:
        if t.build.is_dir():
            victims += [p for p in t.build.iterdir() if p.name.startswith((".scratch-", ".compose-")) and _old(p, hours)]
        victims += [p for p in t.dir.glob(".out.partial-*") if _old(p, hours)]
    removed = []
    for p in victims:
        size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size
        removed.append({"path": str(p.relative_to(root)), "bytes": size})
        if apply:
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
    env.extra.update({"removed": removed, "bytes": sum(x["bytes"] for x in removed), "applied": apply})


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    if args.action == "gc":
        gc(env, target, args.apply, args.hours)
        return
    if args.action == "adopt":
        if target.level != "topic" or not args.upload_key:
            raise Fail("USAGE", "adopt works on one topic and needs --upload-key.")

        def fn(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config) -> None:
            adopt_topic(t, r, tp, cfg, args.upload_key, args.replace)
    else:
        def fn(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config) -> None:
            migrate_topic(t, r, tp, cfg, args.apply, args.delete)

    run_topics(env, target, "media", args.lang, fn, jobs=args.jobs, write_step=False)
