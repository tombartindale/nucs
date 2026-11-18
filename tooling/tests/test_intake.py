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


# -- images embedded as data: URIs -------------------------------------------------------------

def png(seed: bytes) -> bytes:
    """A real 1x1 PNG whose pixel depends on `seed`, so different seeds give different files."""
    import struct
    import zlib

    def chunk(typ: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + typ + body + struct.pack(">I", zlib.crc32(typ + body) & 0xFFFFFFFF)
    import hashlib
    pixel = bytes([0]) + hashlib.sha256(seed).digest()[:3]
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixel)) + chunk(b"IEND", b""))


def _uri(data: bytes, kind: str = "png") -> str:
    import base64
    return f"data:image/{kind};base64,{base64.b64encode(data).decode()}"


def with_embedded(topic_id: str, *images: bytes) -> str:
    """topic_md(), with each image embedded (as a data: URI) on its first slide, in order."""
    refs = "".join(f"![fig]({_uri(d)})\n\n" for d in images)
    return topic_md(topic_id).replace("# Slide 1 heading\n\n", f"# Slide 1 heading\n\n{refs}", 1)


def test_embedded_images_are_extracted_into_assets(tree, tmp_path, capsys):
    src = tmp_path / "paste.md"
    src.write_text(with_embedded("KV7015-U01-T02", png(b"image A"), png(b"image B"), png(b"image A")))
    code, env = bcn(capsys, "intake", str(tree / "KV7015"), "--from", str(src))
    assert code == 0, env["diagnostics"]
    assets = tree / "KV7015/U01/T02/assets"
    assert (assets / "fig-1.png").read_bytes() == png(b"image A")
    assert (assets / "fig-2.png").read_bytes() == png(b"image B")
    assert sorted(p.name for p in assets.iterdir()) == ["fig-1.png", "fig-2.png"]  # the repeat is not a third file
    text = (tree / "KV7015/U01/T02/topic.md").read_text()
    assert "data:image" not in text
    assert text.count("](assets/fig-1.png)") == 2 and "](assets/fig-2.png)" in text
    assert [d["code"] for d in env["diagnostics"]].count("INTAKE_IMAGE_EXTRACTED") == 2
    assert not any(d["code"] == "MD_ASSET_MISSING" for d in env["diagnostics"])


def test_embedded_images_match_existing_assets_by_content_not_position(tree, tmp_path, capsys):
    # As in KV7015-U02-T05: the stored topic shows fig-02 before fig-01. A re-send that embeds
    # the same two images must map each back to its own file by content, and so be unchanged.
    assets = tree / "KV7015/U01/T02/assets"
    assets.mkdir(parents=True)
    (assets / "fig-01.png").write_bytes(png(b"first figure"))
    (assets / "fig-02.png").write_bytes(png(b"second figure"))
    stored = topic_md("KV7015-U01-T02").replace(
        "# Slide 1 heading\n\n", "# Slide 1 heading\n\n![fig](assets/fig-02.png)\n\n![fig](assets/fig-01.png)\n\n", 1)
    (tree / "KV7015/U01/T02/topic.md").write_text(stored)
    src = tmp_path / "paste.md"
    src.write_text(with_embedded("KV7015-U01-T02", png(b"second figure"), png(b"first figure")))
    code, env = bcn(capsys, "intake", str(tree / "KV7015"), "--from", str(src))
    assert env["results"][0]["action"] == "unchanged", env["results"][0]
    assert sorted(p.name for p in assets.iterdir()) == ["fig-01.png", "fig-02.png"]

    # A new image alongside them continues the numbering, padded the same way.
    src.write_text(with_embedded("KV7015-U01-T02", png(b"second figure"), png(b"first figure"), png(b"third figure")))
    code, env = bcn(capsys, "intake", str(tree / "KV7015"), "--from", str(src), "--replace")
    assert env["results"][0]["action"] == "replaced"
    assert (assets / "fig-03.png").read_bytes() == png(b"third figure")


def test_embedded_images_dry_run_writes_nothing(tree, tmp_path, capsys):
    src = tmp_path / "paste.md"
    src.write_text(with_embedded("KV7015-U01-T02", png(b"image A")))
    code, env = bcn(capsys, "intake", str(tree / "KV7015"), "--from", str(src), "--dry-run")
    assert env["results"][0]["action"] == "would_write"
    assert any(d["code"] == "INTAKE_IMAGE_EXTRACTED" for d in env["diagnostics"])
    assert not (tree / "KV7015/U01/T02").exists() or not (tree / "KV7015/U01/T02/assets").exists()


def test_a_damaged_embedded_image_refuses_the_topic(tree, tmp_path, capsys):
    good = png(b"intact")
    src = tmp_path / "paste.md"
    src.write_text(with_embedded("KV7015-U01-T02", good[:-10]))  # cut off, as long pastes are
    code, env = bcn(capsys, "intake", str(tree / "KV7015"), "--from", str(src))
    assert env["results"][0]["action"] == "refused"
    assert any(d["code"] == "INTAKE_IMAGE_CORRUPT" for d in env["diagnostics"])
    assert not (tree / "KV7015/U01/T02/topic.md").exists()


def test_embedded_image_type_comes_from_its_bytes_and_url_safe_base64_is_accepted(tree, tmp_path, capsys):
    import base64
    jpeg = b"\xff\xd8\xff\xe0" + b"jpeg body bytes" * 4
    uri = "data:image/png;charset=utf-8;base64," + base64.urlsafe_b64encode(jpeg).decode().rstrip("=")
    src = tmp_path / "paste.md"
    src.write_text(topic_md("KV7015-U01-T02").replace("# Slide 1 heading\n\n", f"# Slide 1 heading\n\n![photo]({uri})\n\n", 1))
    code, env = bcn(capsys, "intake", str(tree / "KV7015"), "--from", str(src))
    assert code == 0, env["diagnostics"]
    assert (tree / "KV7015/U01/T02/assets/fig-1.jpg").read_bytes() == jpeg
    assert "![photo](assets/fig-1.jpg)" in (tree / "KV7015/U01/T02/topic.md").read_text()
