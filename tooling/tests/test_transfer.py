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


def test_media_export_round_trips_through_import(tree, capsys):
    # PUSH_NAMES previously had no rule for edit/master.mp4, so local_to_remote() fell back
    # to a name with no module prefix that _module_of() could never parse back on import —
    # the media file silently never round-tripped. Covers the fix in bcn/sync.py.
    (tree / "KV7015" / "U01" / "T01" / "edit").mkdir(parents=True)
    (tree / "KV7015" / "U01" / "T01" / "edit" / "master.mp4").write_bytes(b"not a real video")
    code, export_env = bcn(capsys, "transfer", str(tree / "KV7015"), "--export", "--media")
    assert code == 0, export_env["diagnostics"]
    zpath = tree / export_env["zip"]
    names = _names(zpath)
    assert "KV7015-U01-T01.mp4" in names

    other = tree.parent / "restore"
    other.mkdir()
    (other / "programme.toml").write_text('[programme]\nname = "restored"\n')
    (other / "KV7015").mkdir()
    code, import_env = bcn(capsys, "transfer", str(other), "--import", str(zpath))
    assert code == 0, import_env["diagnostics"]
    result = next(r for r in import_env["results"] if r["topic"] == "KV7015-U01-T01.mp4")
    assert result["action"] == "written"
    assert (other / "KV7015" / "U01" / "T01" / "edit" / "master.mp4").read_bytes() == b"not a real video"


def test_full_export_requires_root_scope(tree, capsys):
    code, env = bcn(capsys, "transfer", str(tree / "KV7015"), "--export", "--full")
    assert code == 2
    assert any(d["code"] == "USAGE" for d in env["diagnostics"])


def test_full_export_includes_programme_toml_and_media(tree, capsys):
    code, env = bcn(capsys, "transfer", str(tree), "--export", "--full")
    assert code == 0, env["diagnostics"]
    names = _names(tree / env["zip"])
    assert "programme.toml" in names
    assert "KV7015-U01-T01.md" in names
    assert "KV7015-course-map.md" in names


def test_full_export_includes_custom_theme(tree, capsys):
    theme = tree / "themes" / "custom"
    (theme / "fonts").mkdir(parents=True)
    (theme / "theme.toml").write_text('css = "x.css"\n')
    (theme / "fonts" / "Face.otf").write_bytes(b"not a real font")
    code, env = bcn(capsys, "transfer", str(tree), "--export", "--full")
    assert code == 0, env["diagnostics"]
    names = _names(tree / env["zip"])
    assert "themes/custom/theme.toml" in names
    assert "themes/custom/fonts/Face.otf" in names


def test_full_import_onto_existing_programme_protects_programme_toml(tree, tmp_path, capsys):
    # Importing a --full backup onto an already-configured programme (no --init) must not
    # silently clobber its real programme.toml -- same never-overwrite-a-difference rule
    # as any other file. themes/custom/, having no file there yet, still gets written.
    zpath = tmp_path / "full.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("programme.toml", '[programme]\nname = "restored"\n')
        z.writestr("themes/custom/theme.toml", 'css = "x.css"\n')
    before = (tree / "programme.toml").read_text()
    code, env = bcn(capsys, "transfer", str(tree), "--import", str(zpath), "--full")
    assert code == 1
    result = next(r for r in env["results"] if r["topic"] == "programme.toml")
    assert result["action"] == "exists_differs"
    assert (tree / "programme.toml").read_text() == before
    assert (tree / "themes" / "custom" / "theme.toml").read_text() == 'css = "x.css"\n'


def test_full_import_init_bootstraps_a_fresh_root(tmp_path, capsys):
    # A genuinely empty disaster-recovery target: no programme.toml exists at all yet, so
    # resolve() would otherwise fail with ROOT_NOT_FOUND before --full's own files are ever
    # read. --init creates the root and a placeholder programme.toml first (same idea as
    # `bcn sync --init`), then --full's real programme.toml overwrites that placeholder.
    root = tmp_path / "fresh-root"
    zpath = tmp_path / "full.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("programme.toml", '[programme]\nname = "restored"\n')
        z.writestr("KV7015-course-map.md", "# KV7015 Test module\n")
    code, env = bcn(capsys, "transfer", str(root), "--import", str(zpath), "--full", "--init")
    assert code == 0, env["diagnostics"]
    assert (root / "programme.toml").read_text() == '[programme]\nname = "restored"\n'
    assert (root / "KV7015" / "course-map.md").read_text() == "# KV7015 Test module\n"


def test_init_requires_full_import(tmp_path, capsys):
    root = tmp_path / "fresh-root"
    code, env = bcn(capsys, "transfer", str(root), "--export", "--init")
    assert code == 2
    assert any(d["code"] == "USAGE" for d in env["diagnostics"])


def test_init_does_not_clobber_an_existing_root(tree, tmp_path, capsys):
    zpath = tmp_path / "full.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("programme.toml", '[programme]\nname = "restored"\n')
    before = (tree / "programme.toml").read_text()
    code, env = bcn(capsys, "transfer", str(tree), "--import", str(zpath), "--full", "--init")
    # --init found an existing programme.toml and left it alone (not a placeholder it just
    # created), so it is protected exactly like any --full import onto an existing
    # programme: the real content differs, so it is refused rather than overwritten.
    assert code == 1
    result = next(r for r in env["results"] if r["topic"] == "programme.toml")
    assert result["action"] == "exists_differs"
    assert (tree / "programme.toml").read_text() == before


def test_import_refuses_full_extras_without_full_flag(tree, tmp_path, capsys):
    zpath = tmp_path / "full.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("programme.toml", '[programme]\nname = "attacker"\n')
    before = (tree / "programme.toml").read_text()
    code, env = bcn(capsys, "transfer", str(tree), "--import", str(zpath))
    assert code == 1
    result = next(r for r in env["results"] if r["topic"] == "programme.toml")
    assert result["action"] == "refused"
    assert (tree / "programme.toml").read_text() == before


def test_full_import_requires_root_scope(tree, tmp_path, capsys):
    zpath = tmp_path / "full.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("KV7015-U01-T02.md", topic_md("KV7015-U01-T02"))
    code, env = bcn(capsys, "transfer", str(tree / "KV7015"), "--import", str(zpath), "--full")
    assert code == 2
    assert any(d["code"] == "USAGE" for d in env["diagnostics"])


def test_full_round_trip_is_unchanged(tree, capsys):
    (tree / "themes" / "custom").mkdir(parents=True)
    (tree / "themes" / "custom" / "theme.toml").write_text('css = "x.css"\n')
    code, env = bcn(capsys, "transfer", str(tree), "--export", "--full")
    zpath = tree / env["zip"]
    code, env = bcn(capsys, "transfer", str(tree), "--import", str(zpath), "--full")
    assert code == 0, env["diagnostics"]
    actions = {r["action"] for r in env["results"]}
    assert actions == {"unchanged"}
