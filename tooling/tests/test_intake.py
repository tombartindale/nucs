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
