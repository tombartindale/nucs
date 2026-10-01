"""bcn transfer: a one-shot, stateless whole-tree export/import (no live shared folder)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from bcn.cli import main

from .conftest import topic_md


def bcn(capsys, *args):
    code = main([*args, "--quiet"])
    return code, json.loads(capsys.readouterr().out)


def _names(zpath: Path) -> set[str]:
    with zipfile.ZipFile(zpath) as z:
        return set(z.namelist())


def test_export_flat_names_every_content_type(tree, capsys):
    code, env = bcn(capsys, "transfer", str(tree / "KV7015"), "--export")
    assert code == 0, env["diagnostics"]
    zpath = tree / env["zip"]
    names = _names(zpath)
    assert names == {"KV7015-U01-T01.md", "KV7015-U01-activity.md", "KV7015-course-map.md", "manifest.json"}


def test_export_nested_layout(tree, capsys):
    code, env = bcn(capsys, "transfer", str(tree / "KV7015"), "--export", "--nested")
    assert code == 0, env["diagnostics"]
    names = _names(tree / env["zip"])
    assert names == {"KV7015/U01/T01/topic.md", "KV7015/U01/activity.md", "KV7015/course-map.md", "manifest.json"}


def test_export_unit_scope_excludes_other_units_and_course_map(tree, capsys):
    (tree / "KV7015" / "U02").mkdir(parents=True)
    (tree / "KV7015" / "U02" / "activity.md").write_text("# U02 Activity\n")
    code, env = bcn(capsys, "transfer", str(tree / "KV7015" / "U01"), "--export")
    assert code == 0, env["diagnostics"]
    names = _names(tree / env["zip"])
    # Scoped to U01: no course-map.md (module-level) and no U02 activity.
    assert names == {"KV7015-U01-T01.md", "KV7015-U01-activity.md", "manifest.json"}


def test_export_nothing_in_scope(tree, capsys):
    (tree / "KV7015" / "U02").mkdir(parents=True)
    code, env = bcn(capsys, "transfer", str(tree / "KV7015" / "U02"), "--export")
    assert code == 1
    assert any(d["code"] == "XFER_NOTHING_TO_EXPORT" for d in env["diagnostics"])


def test_import_flat_zip_places_files(tree, tmp_path, capsys):
    zpath = tmp_path / "batch.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("KV7015-U01-T02.md", topic_md("KV7015-U01-T02", title="Another"))
    code, env = bcn(capsys, "transfer", str(tree / "KV7015"), "--import", str(zpath))
    assert code == 0, env["diagnostics"]
    result = next(r for r in env["results"] if r["topic"] == "KV7015-U01-T02.md")
    assert result["action"] == "written"
    assert (tree / "KV7015" / "U01" / "T02" / "topic.md").read_text() == topic_md("KV7015-U01-T02", title="Another")


def test_import_nested_zip_places_files(tree, tmp_path, capsys):
    zpath = tmp_path / "batch.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("KV7015/U01/T02/topic.md", topic_md("KV7015-U01-T02", title="Another"))
    code, env = bcn(capsys, "transfer", str(tree / "KV7015"), "--import", str(zpath))
    assert code == 0, env["diagnostics"]
    assert (tree / "KV7015" / "U01" / "T02" / "topic.md").is_file()


def test_import_unchanged_round_trip(tree, tmp_path, capsys):
    code, env = bcn(capsys, "transfer", str(tree / "KV7015"), "--export")
    zpath = tree / env["zip"]
    code, env = bcn(capsys, "transfer", str(tree / "KV7015"), "--import", str(zpath))
    assert code == 0, env["diagnostics"]
    actions = {r["action"] for r in env["results"]}
    assert actions == {"unchanged"}


def test_import_never_overwrites_a_differing_file(tree, tmp_path, capsys):
    zpath = tmp_path / "batch.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("KV7015-U01-T01.md", topic_md(say=False))  # different content, same topic
    before = (tree / "KV7015" / "U01" / "T01" / "topic.md").read_text()
    code, env = bcn(capsys, "transfer", str(tree / "KV7015"), "--import", str(zpath))
    assert code == 1
    result = next(r for r in env["results"] if r["topic"] == "KV7015-U01-T01.md")
    assert result["action"] == "exists_differs"
    assert "diff" in result
    assert (tree / "KV7015" / "U01" / "T01" / "topic.md").read_text() == before, "the file on disk must never change"


def test_import_unrecognized_file_is_reported_not_placed(tree, tmp_path, capsys):
    zpath = tmp_path / "batch.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("some-random-file.docx", "not a real docx")
    code, env = bcn(capsys, "transfer", str(tree / "KV7015"), "--import", str(zpath))
    assert code == 1
    result = next(r for r in env["results"] if r["topic"] == "some-random-file.docx")
    assert result["action"] == "unrecognized"
    assert not (tree / "some-random-file.docx").exists()


def test_import_out_of_scope_file_is_refused(tree, tmp_path, capsys):
    (tree / "KV7015" / "U02").mkdir(parents=True)
    zpath = tmp_path / "batch.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("KV7015-U02-T01.md", topic_md("KV7015-U02-T01"))
    code, env = bcn(capsys, "transfer", str(tree / "KV7015" / "U01"), "--import", str(zpath))
    assert code == 1
    result = next(r for r in env["results"] if r["topic"] == "KV7015-U02-T01.md")
    assert result["action"] == "refused"
    assert not (tree / "KV7015" / "U02" / "T01" / "topic.md").exists()


def test_import_dry_run_writes_nothing(tree, tmp_path, capsys):
    zpath = tmp_path / "batch.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("KV7015-U01-T02.md", topic_md("KV7015-U01-T02", title="Another"))
    code, env = bcn(capsys, "transfer", str(tree / "KV7015"), "--import", str(zpath), "--dry-run")
    assert code == 0, env["diagnostics"]
    result = next(r for r in env["results"] if r["topic"] == "KV7015-U01-T02.md")
    assert result["action"] == "would_write"
    assert not (tree / "KV7015" / "U01" / "T02" / "topic.md").exists()
