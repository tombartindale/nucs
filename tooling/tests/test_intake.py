"""bcn intake: placing pasted/batched topic markdown, and the assets that come with it."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from bcn.cli import main

from .conftest import topic_md


def bcn(capsys, *args):
    code = main([*args, "--quiet"])
    return code, json.loads(capsys.readouterr().out)


def with_image(topic_id: str, name: str = "diagram.png") -> str:
    """topic_md(), with an image reference added to its first slide."""
    text = topic_md(topic_id)
    return text.replace("# Slide 1 heading\n\n", f"# Slide 1 heading\n\n![diagram](assets/{name})\n\n", 1)


def add_t03(tree: Path) -> None:
    """The fixture's course-map only has T01/T02; a couple of tests need a third topic."""
    path = tree / "KV7015" / "course-map.md"
    path.write_text(path.read_text().rstrip() + "\n| T03 | Third | 4 | LO1 |\n")


# T02 is in the fixture's course map but has no topic.md on disk yet (only T01 does), so
# intaking it is always a fresh "written", never blocked by pre-existing fixture content.


def test_zip_assets_beside_topic_md_are_written_and_validate_passes(tree, tmp_path, capsys):
    zpath = tmp_path / "batch.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("U01/T02/topic.md", with_image("KV7015-U01-T02"))
        z.writestr("U01/T02/assets/diagram.png", b"not a real png, just bytes")
    code, env = bcn(capsys, "intake", str(tree / "KV7015"), "--from", str(zpath))
    r = env["results"][0]
    assert r["action"] == "written", env["diagnostics"]
    assert (tree / "KV7015/U01/T02/assets/diagram.png").read_bytes() == b"not a real png, just bytes"
    assert any(d["code"] == "INTAKE_ASSET_WRITTEN" for d in env["diagnostics"])
    # The whole point: validate must not report the image as missing, since it came in too.
    assert not any(d["code"] == "MD_ASSET_MISSING" for d in env["diagnostics"])
    assert r["validate_ok"] is True
    assert code == 0


def test_asset_missing_without_the_fix_would_fail_validate(tree, tmp_path, capsys):
    # Same topic, but the zip has no assets/ folder at all: confirms the test above is
    # actually exercising the fix, not something validate would pass regardless.
    zpath = tmp_path / "batch.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("U01/T02/topic.md", with_image("KV7015-U01-T02"))
    code, env = bcn(capsys, "intake", str(tree / "KV7015"), "--from", str(zpath))
    r = env["results"][0]
    assert any(d["code"] == "MD_ASSET_MISSING" for d in env["diagnostics"])
    assert r["validate_ok"] is False
    assert code == 1


def test_zip_without_any_assets_folder_is_unaffected(tree, tmp_path, capsys):
    zpath = tmp_path / "batch.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("U01/T02/topic.md", topic_md("KV7015-U01-T02"))
    code, env = bcn(capsys, "intake", str(tree / "KV7015"), "--from", str(zpath))
    assert code == 0, env["diagnostics"]
    assert env["results"][0]["action"] == "written"
    assert not (tree / "KV7015/U01/T02/assets").exists()


def test_zip_preserves_directory_structure_so_same_named_files_do_not_collide(tree, tmp_path, capsys):
    # Flattening every .md to just its basename during extraction (the old behaviour) would
    # make two files both named topic.md from different folders overwrite one another
    # before either was even read. Nesting under U01/T02 and U01/T03 must keep both.
    add_t03(tree)
    zpath = tmp_path / "batch.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("U01/T02/topic.md", with_image("KV7015-U01-T02", "a.png"))
        z.writestr("U01/T02/assets/a.png", b"first")
        z.writestr("U01/T03/topic.md", with_image("KV7015-U01-T03", "a.png"))
        z.writestr("U01/T03/assets/a.png", b"second")
    code, env = bcn(capsys, "intake", str(tree / "KV7015"), "--from", str(zpath))
    assert code == 0, env["diagnostics"]
    assert {r["topic"] for r in env["results"]} == {"KV7015-U01-T02", "KV7015-U01-T03"}
    assert (tree / "KV7015/U01/T02/assets/a.png").read_bytes() == b"first"
    assert (tree / "KV7015/U01/T03/assets/a.png").read_bytes() == b"second"


def test_flat_zip_with_a_shared_top_level_assets_folder_matches_by_filename(tree, tmp_path, capsys):
    # The real-world case this was missing: .md files flat-named at the top level (bcn
    # sync's OneDrive naming), with images in a shared assets/ folder in the same zip --
    # not nested beside any one .md file. Matched by the filename each topic references.
    zpath = tmp_path / "batch.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("KV7015-U01-T02.md", with_image("KV7015-U01-T02"))
        z.writestr("assets/diagram.png", b"shared image bytes")
    code, env = bcn(capsys, "intake", str(tree / "KV7015"), "--from", str(zpath))
    r = env["results"][0]
    assert r["action"] == "written", env["diagnostics"]
    assert (tree / "KV7015/U01/T02/assets/diagram.png").read_bytes() == b"shared image bytes"
    assert r["validate_ok"] is True
    assert code == 0


def test_assets_supplied_as_a_separate_zip_are_matched_too(tree, tmp_path, capsys):
    # "A separate assets folder/zip": images never in the same upload as the .md at all.
    md_zip = tmp_path / "batch.zip"
    with zipfile.ZipFile(md_zip, "w") as z:
        z.writestr("KV7015-U01-T02.md", with_image("KV7015-U01-T02"))
    assets_zip = tmp_path / "assets.zip"
    with zipfile.ZipFile(assets_zip, "w") as z:
        z.writestr("diagram.png", b"from the separate zip")
    code, env = bcn(capsys, "intake", str(tree / "KV7015"), "--from", str(md_zip), "--assets", str(assets_zip))
    r = env["results"][0]
    assert r["action"] == "written", env["diagnostics"]
    assert (tree / "KV7015/U01/T02/assets/diagram.png").read_bytes() == b"from the separate zip"
    assert r["validate_ok"] is True
    assert code == 0


def test_ambiguous_asset_name_across_the_source_is_reported(tree, tmp_path, capsys):
    add_t03(tree)
    zpath = tmp_path / "batch.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("KV7015-U01-T02.md", with_image("KV7015-U01-T02", "a.png"))
        z.writestr("other/a.png", b"candidate one")
        z.writestr("another/a.png", b"candidate two")
    code, env = bcn(capsys, "intake", str(tree / "KV7015"), "--from", str(zpath))
    assert any(d["code"] == "INTAKE_ASSET_AMBIGUOUS" for d in env["diagnostics"])
    assert (tree / "KV7015/U01/T02/assets/a.png").is_file()  # still placed, just noted as ambiguous


def test_replace_overwrites_a_differing_topic_file(tree, tmp_path, capsys):
    # T01 already has fixture content on disk; pasting different text without --replace
    # is refused (covered by test_cli.py's test_intake_refuses_to_overwrite), but with it
    # the new text wins.
    paste = tmp_path / "paste.md"
    paste.write_text(topic_md().replace("Point one", "Point uno"))
    code, env = bcn(capsys, "intake", str(tree / "KV7015"), "--from", str(paste), "--replace")
    r = env["results"][0]
    assert code == 0, env["diagnostics"]
    assert r["action"] == "replaced"
    assert any(d["code"] == "INTAKE_REPLACED" for d in env["diagnostics"])
    assert "Point uno" in (tree / "KV7015/U01/T01/topic.md").read_text()


def test_replace_with_dry_run_writes_nothing(tree, tmp_path, capsys):
    paste = tmp_path / "paste.md"
    paste.write_text(topic_md().replace("Point one", "Point uno"))
    before = (tree / "KV7015/U01/T01/topic.md").read_text()
    code, env = bcn(capsys, "intake", str(tree / "KV7015"), "--from", str(paste), "--replace", "--dry-run")
    assert env["results"][0]["action"] == "would_replace"
    assert (tree / "KV7015/U01/T01/topic.md").read_text() == before
