"""Object storage for media: pointers, migrate, compose from a remote master, package, adopt, gc."""

from __future__ import annotations

import json
import os
import time

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from bcn import fsutil, storage  # noqa: E402
from bcn.tree import Topic  # noqa: E402

from .conftest import make_video  # noqa: E402
from .test_cli import bcn  # noqa: E402
from .test_compose import _build_up_to_cues  # noqa: E402

BUCKET = "bcn-test-media"


@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("S3_MEDIA_BUCKET", BUCKET)
    monkeypatch.delenv("S3_MEDIA_ENDPOINT", raising=False)
    monkeypatch.delenv("BCN_SCRATCH", raising=False)
    with moto.mock_aws():
        client = boto3.client("s3")
        client.create_bucket(Bucket=BUCKET)
        yield client


def keys(client) -> set[str]:
    return {o["Key"] for o in client.list_objects_v2(Bucket=BUCKET).get("Contents", [])}


def test_storage_off_without_bucket(tree, monkeypatch):
    monkeypatch.delenv("S3_MEDIA_BUCKET", raising=False)
    assert storage.remote(tree) is None


def test_migrate_dry_run_then_apply(tree, capsys, s3):
    t = Topic(tree, "KV7015", "U01", "T01")
    make_video(t.video, 1)
    size = t.video.stat().st_size
    code, env = bcn(capsys, "media", str(t.dir), "migrate")
    assert code == 0, env["diagnostics"]
    assert env["results"][0]["files"][0]["action"] == "would_upload"
    assert t.video.is_file() and not keys(s3)

    code, env = bcn(capsys, "media", str(t.dir), "migrate", "--apply", "--delete")
    assert code == 0, env["diagnostics"]
    assert not t.video.exists()
    ptr = storage.read_pointer(t.video)
    assert ptr and ptr["size"] == size and ptr["key"] == "media/KV7015/U01/T01/edit/master.mp4"
    assert ptr["duration"] and abs(ptr["duration"] - 1) < 0.5
    assert keys(s3) == {ptr["key"]}
    assert storage.present(t.video) and storage.size(t.video) == size


def test_migrate_keeps_mtime_so_steps_stay_fresh(tree, capsys, s3):
    t = Topic(tree, "KV7015", "U01", "T01")
    make_video(t.video, 1)
    old = time.time() - 3600
    os.utime(t.video, (old, old))
    bcn(capsys, "media", str(t.dir), "migrate", "--apply", "--delete")
    assert abs(fsutil.mtime(t.video) - old) < 1


def test_pointer_not_reported_as_unexpected(tree, capsys, s3):
    t = Topic(tree, "KV7015", "U01", "T01")
    make_video(t.video, 1)
    bcn(capsys, "media", str(t.dir), "migrate", "--apply", "--delete")
    code, env = bcn(capsys, "validate", str(t.dir))
    assert not [d for d in env["diagnostics"] if d["code"].startswith("FS_")]


def test_compose_and_package_from_remote_master(tree, capsys, s3):
    t = Topic(tree, "KV7015", "U01", "T01")
    _build_up_to_cues(tree, capsys, t)
    code, env = bcn(capsys, "media", str(t.dir), "migrate", "--apply", "--delete")
    assert code == 0, env["diagnostics"]
    assert not t.video.exists()

    # cues is still fresh against the pointer (same mtime as the file it replaced)
    code, env = bcn(capsys, "cues", str(t.dir))
    assert code == 0, env["diagnostics"]
    assert env["results"][0]["skipped"] is True

    code, env = bcn(capsys, "compose", str(t.dir))
    assert code == 0, env["diagnostics"]
    assert not t.composed("en").exists(), "the composed video lives in the bucket, not on disk"
    ptr = storage.read_pointer(t.composed("en"))
    assert ptr and ptr["key"] in keys(s3) and ptr["size"] > 0
    assert any(a["kind"] == "composed" and a["bytes"] == ptr["size"] for a in env["artifacts"])
    assert not list(t.build.glob(".scratch-*")) and not list(t.build.glob(".compose-*"))
    assert not list(t.build.glob("**/*.mp4")), "no video left on local disk after compose"

    code, env = bcn(capsys, "package", str(t.dir))
    assert code == 0, env["diagnostics"]
    assert not list(t.out.glob("*.mp4"))
    out_ptr = storage.read_pointer(t.out / f"{t.id}.mp4")
    assert out_ptr and out_ptr["key"] == f"media/KV7015/U01/T01/out/{t.id}.mp4" and out_ptr["key"] in keys(s3)
    manifest = json.loads(t.manifest("en").read_text())
    entry = next(f for f in manifest["files"] if f["name"] == f"{t.id}.mp4")
    assert entry["remote"] is True and entry["bytes"] == out_ptr["size"]
    assert abs(manifest["duration"] - ptr["duration"]) < 0.01

    # Compose again: skipped as fresh, no re-download needed.
    code, env = bcn(capsys, "compose", str(t.dir))
    assert code == 0 and env["results"][0]["skipped"] is True


def test_compose_failure_leaves_no_scratch(tree, capsys, s3, monkeypatch):
    t = Topic(tree, "KV7015", "U01", "T01")
    _build_up_to_cues(tree, capsys, t)
    bcn(capsys, "media", str(t.dir), "migrate", "--apply", "--delete")
    import bcn.commands.compose as compose

    def boom(*a, **k):
        raise RuntimeError("encoder exploded")

    monkeypatch.setattr(compose, "ffmpeg", boom)
    code, env = bcn(capsys, "compose", str(t.dir), "--force")
    assert code != 0
    assert not list(t.build.glob(".scratch-*")) and not list(t.build.glob(".compose-*"))


def test_workspace_uses_bcn_scratch(tree, s3, monkeypatch, tmp_path):
    t = Topic(tree, "KV7015", "U01", "T01")
    make_video(t.video, 1)
    storage.remote(tree).put(t.video, t.video, delete_local=True)
    scratch = tmp_path / "scratch"
    monkeypatch.setenv("BCN_SCRATCH", str(scratch))
    with storage.workspace(tree, [t.video], t.build) as m:
        assert m[t.video].is_file() and scratch in m[t.video].parents
    assert not list(scratch.iterdir())


def test_adopt_upload(tree, capsys, s3):
    t = Topic(tree, "KV7015", "U01", "T01")
    make_video(t.video, 1)
    src = t.dir / "x.mp4"
    t.video.rename(src)
    inc = "media/_incoming/abc123/master.mp4"
    s3.upload_file(str(src), BUCKET, inc)

    code, env = bcn(capsys, "media", str(t.dir), "adopt", "--upload-key", inc)
    assert code == 0, env["diagnostics"]
    ptr = storage.read_pointer(t.video)
    assert ptr and ptr["key"] == "media/KV7015/U01/T01/edit/master.mp4"
    assert keys(s3) == {ptr["key"]}, "staging object is removed"

    # Replacing needs --replace
    s3.upload_file(str(src), BUCKET, inc)
    code, env = bcn(capsys, "media", str(t.dir), "adopt", "--upload-key", inc)
    assert code != 0
    assert inc in keys(s3), "a refused upload is left for the caller to discard"
    code, env = bcn(capsys, "media", str(t.dir), "adopt", "--upload-key", inc, "--replace")
    assert code == 0, env["diagnostics"]


def test_adopt_rejects_non_mp4_and_foreign_keys(tree, capsys, s3):
    t = Topic(tree, "KV7015", "U01", "T01")
    s3.put_object(Bucket=BUCKET, Key="media/_incoming/z/master.mp4", Body=b"this is plainly not a video file")
    code, env = bcn(capsys, "media", str(t.dir), "adopt", "--upload-key", "media/_incoming/z/master.mp4")
    assert code != 0 and not storage.present(t.video)
    assert "media/_incoming/z/master.mp4" not in keys(s3)
    code, env = bcn(capsys, "media", str(t.dir), "adopt", "--upload-key", "media/KV7015/other.mp4")
    assert code != 0


def test_gc(tree, capsys):
    inc = tree / "transfer" / "incoming" / "media-20200101"
    inc.mkdir(parents=True)
    (inc / "KV7015-U01-T01.mp4").write_bytes(b"x" * 100)
    fresh = tree / "transfer" / "incoming" / "media-now"
    fresh.mkdir()
    old = time.time() - 3 * 86400
    os.utime(inc, (old, old))
    code, env = bcn(capsys, "media", str(tree), "gc")
    assert env["removed"] and inc.exists(), "dry run removes nothing"
    code, env = bcn(capsys, "media", str(tree), "gc", "--apply")
    assert not inc.exists() and fresh.exists()


def test_transfer_import_single_video_goes_to_bucket(tree, capsys, s3, tmp_path):
    t = Topic(tree, "KV7015", "U01", "T01")
    src = tmp_path / "KV7015-U01-T01.mp4"
    make_video(src, 1)
    code, env = bcn(capsys, "transfer", str(tree), "--import", str(src))
    assert code == 0, env["diagnostics"]
    assert not t.video.exists() and storage.read_pointer(t.video)
    assert "media/KV7015/U01/T01/edit/master.mp4" in keys(s3)
    # Same content again is unchanged; different content is refused without --replace.
    code, env = bcn(capsys, "transfer", str(tree), "--import", str(src))
    assert code == 0 and env["results"][0]["action"] == "unchanged"
    other = tmp_path / "other" / "KV7015-U01-T01.mp4"
    other.parent.mkdir()
    make_video(other, 2)
    code, env = bcn(capsys, "transfer", str(tree), "--import", str(other))
    assert code != 0 and env["results"][0]["action"] == "exists_differs"
    code, env = bcn(capsys, "transfer", str(tree), "--import", str(other), "--replace")
    assert code == 0, env["diagnostics"]


def test_transfer_export_media_includes_remote_master(tree, capsys, s3):
    import zipfile
    t = Topic(tree, "KV7015", "U01", "T01")
    make_video(t.video, 1)
    size = t.video.stat().st_size
    bcn(capsys, "media", str(t.dir), "migrate", "--apply", "--delete")
    code, env = bcn(capsys, "transfer", str(tree), "--export", "--media")
    assert code == 0, env["diagnostics"]
    with zipfile.ZipFile(tree / env["zip"]) as z:
        assert z.getinfo("KV7015-U01-T01.mp4").file_size == size
    assert not list((tree / "transfer" / "exports").glob(".scratch-*"))


def test_status_verify_checks_remote_object(tree, capsys, s3):
    t = Topic(tree, "KV7015", "U01", "T01")
    make_video(t.video, 1)
    bcn(capsys, "media", str(t.dir), "migrate", "--apply", "--delete")
    code, env = bcn(capsys, "status", str(t.dir), "--verify")
    assert not [d for d in env["diagnostics"] if d["code"] in ("FS_MISSING", "FS_CORRUPT")], env["diagnostics"]
    s3.delete_object(Bucket=BUCKET, Key=storage.read_pointer(t.video)["key"])
    code, env = bcn(capsys, "status", str(t.dir), "--verify")
    codes = [d["code"] for d in env["diagnostics"]] + [d["code"] for r in env["results"] for d in r.get("diagnostics", [])]
    assert "FS_MISSING" in codes


def test_show_and_status_still_see_a_migrated_video(tree, capsys, s3):
    """Regression: show reported no video once the master became a pointer, so the UI said none existed."""
    t = Topic(tree, "KV7015", "U01", "T01")
    make_video(t.video, 1)
    code, env = bcn(capsys, "show", str(t.dir))
    assert env["results"][0]["media"]["master"] == "KV7015/U01/T01/edit/master.mp4"
    bcn(capsys, "media", str(t.dir), "migrate", "--apply", "--delete")
    assert not t.video.exists()
    code, env = bcn(capsys, "show", str(t.dir))
    assert env["results"][0]["media"]["master"] == "KV7015/U01/T01/edit/master.mp4"
    code, env = bcn(capsys, "status", str(t.dir))
    arts = env["results"][0]["artifacts"]
    master = next(a for a in arts if a["key"] == "edit/master.mp4")
    assert master["exists"] is True and master["bytes"] > 0
