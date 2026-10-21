"""bcn bundle: snapshot a job for a remote runner, collect its outputs, apply them back."""

from __future__ import annotations

import io
import json
import os
import tarfile
import time
from pathlib import Path

import pytest

from bcn.commands.bundle import OUTPUTS_FILE, REMOTE_SPECS, SNAPSHOT_FILE, _matches
from bcn.tree import Topic

from .test_cli import bcn


def _archive(path: Path, members: dict[str, bytes], deleted: list[str] | None = None) -> Path:
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as tf:
        for name, data in {OUTPUTS_FILE: json.dumps({"deleted": deleted or []}).encode(), **members}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def _extract(archive: Path, dest: Path) -> Path:
    with tarfile.open(archive, "r:gz") as tf:
        tf.extractall(dest, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    return dest


def test_globs_never_cross_a_slash():
    compose = REMOTE_SPECS["compose"]
    assert _matches("build/compose.json", compose) and _matches("build/compose.zh.json", compose)
    assert _matches("build/composed.vertical_text.zh.mp4.s3.json", compose)
    assert _matches("build/subtitles/KV7015-U01-T01.en.delivery.srt", compose)
    assert not _matches("build/composed.mp4", compose), "video itself never travels in an archive"
    assert not _matches("build/subtitles/x/y.delivery.srt", compose)
    assert not _matches("topic.md", compose)
    assert _matches("build/slides/en-full/KV7015-U01-T01-s01.png", REMOTE_SPECS["render"])


def test_specs_lists_remote_commands(tree, capsys):
    code, env = bcn(capsys, "bundle", str(tree), "specs")
    assert code == 0 and env["remote_commands"] == sorted(REMOTE_SPECS)


def test_create_compose_needs_storage(tree, capsys, monkeypatch, tmp_path):
    monkeypatch.delenv("S3_MEDIA_BUCKET", raising=False)
    t = Topic(tree, "KV7015", "U01", "T01")
    code, env = bcn(capsys, "bundle", str(t.dir), "create", "--cmd", "compose", "--archive", str(tmp_path / "in.tgz"))
    assert code != 0 and env["diagnostics"][0]["code"] == "CONFIG_INVALID"


def test_create_refuses_a_local_master(tree, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("S3_MEDIA_BUCKET", "b")
    t = Topic(tree, "KV7015", "U01", "T01")
    t.video.parent.mkdir()
    t.video.write_bytes(b"\0\0\0\x18ftypmp42")
    code, env = bcn(capsys, "bundle", str(t.dir), "create", "--cmd", "compose", "--archive", str(tmp_path / "in.tgz"))
    assert code != 0 and env["diagnostics"][0]["code"] == "BUNDLE_SNAPSHOT"


def test_create_snapshot_contents_keep_mtimes(tree, capsys, tmp_path):
    t = Topic(tree, "KV7015", "U01", "T01")
    (t.build / "slides" / "en").mkdir(parents=True)
    (t.build / "slides" / "en" / "a.png").write_bytes(b"png")
    (t.build / "bumpers").mkdir()
    (t.build / "bumpers" / "x.intro.en.mp4").write_bytes(b"clip")
    (t.build / "composed.mp4").write_bytes(b"video")
    (t.build / ".scratch-1").mkdir()
    (t.build / ".scratch-1" / "master.mp4").write_bytes(b"video")
    (t.dir / ".history").mkdir()
    (t.dir / ".history" / "topic.1.md").write_text("old")
    (t.out).mkdir()
    (t.out / "manifest.json").write_text("{}")
    os.utime(t.src("en"), (1_700_000_000.25, 1_700_000_000.25))
    code, env = bcn(capsys, "bundle", str(t.dir), "create", "--cmd", "bumpers", "--archive", str(tmp_path / "in.tgz"))
    assert code == 0, env["diagnostics"]
    with tarfile.open(tmp_path / "in.tgz") as tf:
        names = set(tf.getnames())
    assert {SNAPSHOT_FILE, "programme.toml", "KV7015/course-map.md", "KV7015/U01/T01/topic.md",
            "KV7015/U01/T01/build/slides/en/a.png", "KV7015/U01/T01/build/bumpers/x.intro.en.mp4"} <= names
    assert not any(n.endswith("composed.mp4") or ".scratch" in n or ".history" in n or "/out/" in n for n in names)
    root = _extract(tmp_path / "in.tgz", tmp_path / "runner")
    assert (root / "KV7015/U01/T01/topic.md").stat().st_mtime == 1_700_000_000.25
    snap = json.loads((root / SNAPSHOT_FILE).read_text())
    assert snap["cmd"] == "bumpers" and snap["topics"] == ["KV7015/U01/T01"] and env["stamp"] == snap["stamp"]


@pytest.mark.parametrize("name,why", [
    ("KV7015/U01/T01/topic.md", "not an output"),
    ("KV7015/U01/T02/build/bumpers.json", "outside the job's topics"),
    ("KV7015/U01/T01/../T01/build/bumpers.json", "not a path inside a topic"),
    ("/etc/passwd", "not a relative path"),
])
def test_apply_rejects_anything_but_the_commands_outputs(tree, capsys, tmp_path, name, why):
    t = Topic(tree, "KV7015", "U01", "T01")
    arc = _archive(tmp_path / "out.tgz", {"KV7015/U01/T01/build/bumpers.json": b"{}", name: b"x"})
    code, env = bcn(capsys, "bundle", str(t.dir), "apply", "--cmd", "bumpers", "--archive", str(arc), "--stamp", "1")
    assert code != 0 and env["diagnostics"][0]["code"] == "BUNDLE_REJECTED" and why in env["diagnostics"][0]["message"]
    assert not (t.build / "bumpers.json").exists(), "nothing is written when any member is rejected"


def test_apply_rejects_a_symlink_member(tree, capsys, tmp_path):
    t = Topic(tree, "KV7015", "U01", "T01")
    arc = tmp_path / "out.tgz"
    with tarfile.open(arc, "w:gz") as tf:
        info = tarfile.TarInfo("KV7015/U01/T01/build/bumpers.json")
        info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
        tf.addfile(info)
    code, env = bcn(capsys, "bundle", str(t.dir), "apply", "--cmd", "bumpers", "--archive", str(arc), "--stamp", "1")
    assert code != 0 and "not a regular file" in env["diagnostics"][0]["message"]


def test_apply_rejects_a_pointer_to_another_key(tree, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("S3_MEDIA_BUCKET", "b")
    t = Topic(tree, "KV7015", "U01", "T01")
    bad = json.dumps({"key": "media/KV7015/U01/T01/edit/master.mp4", "size": 1}).encode()
    arc = _archive(tmp_path / "out.tgz", {"KV7015/U01/T01/build/composed.mp4.s3.json": bad})
    code, env = bcn(capsys, "bundle", str(t.dir), "apply", "--cmd", "compose", "--archive", str(arc), "--stamp", "1")
    assert code != 0 and "points somewhere other" in env["diagnostics"][0]["message"]


def test_apply_stamps_and_deletes(tree, capsys, tmp_path):
    t = Topic(tree, "KV7015", "U01", "T01")
    (t.build / "bumpers").mkdir(parents=True)
    old = t.build / "bumpers" / "old.png"
    old.write_bytes(b"old")
    kept = t.build / "bumpers" / "kept.png"
    kept.write_bytes(b"replaced here after the snapshot")
    stamp = time.time() - 60
    os.utime(old, (stamp - 10, stamp - 10))
    arc = _archive(tmp_path / "out.tgz", {"KV7015/U01/T01/build/bumpers.json": b'{"ok": true}'},
                   deleted=["KV7015/U01/T01/build/bumpers/old.png", "KV7015/U01/T01/build/bumpers/kept.png"])
    code, env = bcn(capsys, "bundle", str(t.dir), "apply", "--cmd", "bumpers", "--archive", str(arc),
                    "--stamp", repr(stamp))
    assert code == 0, env["diagnostics"]
    assert (t.build / "bumpers.json").stat().st_mtime == stamp
    assert not old.exists() and kept.exists()
    assert env["deleted"] == ["KV7015/U01/T01/build/bumpers/old.png"]


# -- the whole trip, with real tools and a mocked bucket ----------------------------------------

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")
from .test_bumpers import _isolated_theme, _needs_tools  # noqa: E402
from .test_compose import _build_up_to_cues  # noqa: E402
from .test_storage import keys, s3  # noqa: E402,F401


def _compose_fresh(capsys, t: Topic) -> bool:
    code, env = bcn(capsys, "status", str(t.dir))
    return env["results"][0]["en"]["steps"]["compose"]["fresh"]


def _remote_compose(capsys, t: Topic, tmp_path: Path) -> float:
    """What a runner does: snapshot here, compose in a separate root, collect, apply here."""
    code, env = bcn(capsys, "bundle", str(t.dir), "create", "--cmd", "compose", "--archive", str(tmp_path / "in.tgz"))
    assert code == 0, env["diagnostics"]
    stamp = env["stamp"]
    remote = _extract(tmp_path / "in.tgz", tmp_path / "runner")
    rt = Topic(remote, t.module, t.unit, t.code)
    code, env = bcn(capsys, "compose", str(rt.dir))
    assert code == 0, env["diagnostics"]
    assert env["results"][0]["skipped"] is False
    code, env = bcn(capsys, "bundle", str(rt.dir), "collect", "--archive", str(tmp_path / "out.tgz"))
    assert code == 0, env["diagnostics"]
    assert any(w.endswith("build/composed.mp4.s3.json") for w in env["written"])
    code, env = bcn(capsys, "bundle", str(t.dir), "apply", "--cmd", "compose", "--archive", str(tmp_path / "out.tgz"),
                    "--stamp", repr(stamp))
    assert code == 0, env["diagnostics"]
    return stamp


def test_remote_compose_round_trip(tree, capsys, s3, tmp_path):  # noqa: F811
    t = Topic(tree, "KV7015", "U01", "T01")
    _build_up_to_cues(tree, capsys, t)
    bcn(capsys, "media", str(t.dir), "migrate", "--apply", "--delete")
    _remote_compose(capsys, t, tmp_path)
    ptr = json.loads((t.build / "composed.mp4.s3.json").read_text())
    assert ptr["key"] == "media/KV7015/U01/T01/build/composed.mp4" and ptr["key"] in keys(s3)
    assert t.composed_srt("en").is_file()
    assert _compose_fresh(capsys, t)
    code, env = bcn(capsys, "compose", str(t.dir))
    assert env["results"][0]["skipped"] is True, "the applied result counts as fresh here"
    code, env = bcn(capsys, "package", str(t.dir))
    assert code == 0, env["diagnostics"]


def test_edit_during_remote_run_shows_stale(tree, capsys, s3, tmp_path):  # noqa: F811
    t = Topic(tree, "KV7015", "U01", "T01")
    _build_up_to_cues(tree, capsys, t)
    bcn(capsys, "media", str(t.dir), "migrate", "--apply", "--delete")
    code, env = bcn(capsys, "bundle", str(t.dir), "create", "--cmd", "compose", "--archive", str(tmp_path / "in.tgz"))
    stamp = env["stamp"]
    remote = _extract(tmp_path / "in.tgz", tmp_path / "runner")
    rt = Topic(remote, t.module, t.unit, t.code)
    bcn(capsys, "compose", str(rt.dir))
    bcn(capsys, "bundle", str(rt.dir), "collect", "--archive", str(tmp_path / "out.tgz"))
    t.cues_csv.write_text(t.cues_csv.read_text())  # an edit here while the runner worked
    code, env = bcn(capsys, "bundle", str(t.dir), "apply", "--cmd", "compose", "--archive", str(tmp_path / "out.tgz"),
                    "--stamp", repr(stamp))
    assert code == 0, env["diagnostics"]
    assert not _compose_fresh(capsys, t)


def test_remote_bumpers_round_trip(tree, capsys, tmp_path):
    """A second REMOTE_SPECS entry: bumpers' clips travel in the archive itself, no bucket."""
    _needs_tools(tree)
    _isolated_theme(tree)
    t = Topic(tree, "KV7015", "U01", "T01")
    code, env = bcn(capsys, "bundle", str(t.dir), "create", "--cmd", "bumpers", "--archive", str(tmp_path / "in.tgz"))
    assert code == 0, env["diagnostics"]
    stamp = env["stamp"]
    rt = Topic(_extract(tmp_path / "in.tgz", tmp_path / "runner"), t.module, t.unit, t.code)
    code, env = bcn(capsys, "bumpers", str(rt.dir))
    assert code == 0, env["diagnostics"]
    bcn(capsys, "bundle", str(rt.dir), "collect", "--archive", str(tmp_path / "out.tgz"))
    code, env = bcn(capsys, "bundle", str(t.dir), "apply", "--cmd", "bumpers", "--archive", str(tmp_path / "out.tgz"),
                    "--stamp", repr(stamp))
    assert code == 0, env["diagnostics"]
    assert t.bumper("intro", "en").stat().st_size > 0 and t.step_file("bumpers").is_file()
    code, env = bcn(capsys, "bumpers", str(t.dir))
    assert env["results"][0]["skipped"] is True
