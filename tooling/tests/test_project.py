"""bcn project: a module's Claude Project pack, rendered from authoring/, and creating a new module."""

from __future__ import annotations

import json
import shutil
import zipfile

import pytest

from bcn.cli import main
from bcn.commands import project


def bcn(capsys, *args):
    code = main([*args, "--quiet"])
    return code, json.loads(capsys.readouterr().out)


def codes(env):
    return [d["code"] for d in env["diagnostics"]]


@pytest.fixture
def authoring(tmp_path, monkeypatch):
    """A private copy of authoring/, so a test can change a template."""
    dest = tmp_path / "authoring"
    shutil.copytree(project.AUTHORING, dest)
    monkeypatch.setattr(project, "AUTHORING", dest)
    return dest


def test_new_module_gets_a_folder_and_a_pack_but_no_course_map(tree, capsys):
    code, env = bcn(capsys, "project", str(tree), "--new", "KV7023", "--title", "Data Things", "--units", "8")
    assert code == 0, env["diagnostics"]
    assert env["created"] == "KV7023"
    mdir = tree / "KV7023"
    assert not (mdir / "course-map.md").exists()
    pack = mdir / "build" / "claude-project"
    instructions = (pack / "instructions.md").read_text()
    assert instructions.startswith("You are working with the leader of KV7023 (Data Things),")
    assert "- Units: 8." in instructions
    assert "{{" not in instructions and "KV7015" not in instructions
    for f in env["results"][0]["files"]:
        assert "KV7015" not in (pack / "knowledge" / f).read_text(), f
    with zipfile.ZipFile(mdir / "build" / "claude-project.zip") as z:
        assert sorted(z.namelist()) == env["results"][0]["files"]
    setup = (pack / "SETUP.md").read_text()
    for doc in project.PRODUCER_DOCS:
        assert doc in setup
    assert "topic.md" in setup and env["results"][0]["version"] in setup


def test_new_refuses_an_existing_or_malformed_code(tree, capsys):
    code, env = bcn(capsys, "project", str(tree), "--new", "KV7015")
    assert code != 0 and codes(env) == ["MODULE_EXISTS"]
    (tree / "kv7024").mkdir()  # differs only by case: still the same module on a case-insensitive disk
    code, env = bcn(capsys, "project", str(tree), "--new", "KV7024")
    assert code != 0 and codes(env) == ["MODULE_EXISTS"]
    code, env = bcn(capsys, "project", str(tree), "--new", "KV70")
    assert code != 0 and codes(env) == ["MODULE_CODE_INVALID"]
    assert not (tree / "KV70").exists()


def test_new_needs_the_programme_root(tree, capsys):
    code, env = bcn(capsys, "project", str(tree / "KV7015"), "--new", "KV7023")
    assert code != 0 and codes(env) == ["USAGE"]
    assert not (tree / "KV7015" / "KV7023").exists()
    code, env = bcn(capsys, "project", str(tree / "KV7024"), "--new", "KV7023")
    assert code != 0 and codes(env) == ["USAGE"]
    assert not (tree / "KV7023").exists() and not (tree / "KV7024").exists()


def test_new_at_the_new_modules_own_path(tree, capsys):
    """The UI's job targets the module being created, so that is where bcn is pointed."""
    code, env = bcn(capsys, "project", str(tree / "KV7023"), "--new", "KV7023")
    assert code == 0, env["diagnostics"]
    assert env["created"] == "KV7023"
    assert (tree / "KV7023" / "build" / "claude-project").is_dir()


def test_regenerating_keeps_units_and_title(tree, capsys):
    bcn(capsys, "project", str(tree), "--new", "KV7023", "--title", "Data Things", "--units", "8")
    code, env = bcn(capsys, "project", str(tree / "KV7023"))
    assert code == 0
    r = env["results"][0]
    assert (r["units"], r["title"]) == (8, "Data Things")


def test_an_existing_module_takes_its_unit_count_from_the_course_map(tree, capsys):
    (tree / "KV7015" / "course-map.md").write_text(
        "# KV7015 — Course map\n\n## U01 — One\n\n## U02 — Two\n\n## U03 — Three\n")
    code, env = bcn(capsys, "project", str(tree / "KV7015"))
    assert code == 0, env["diagnostics"]
    assert env["results"][0]["units"] == 3


def test_status_lists_a_new_module_and_marks_its_pack_stale_when_a_template_changes(tree, capsys, authoring):
    bcn(capsys, "project", str(tree), "--new", "KV7023")
    _, env = bcn(capsys, "status", str(tree))
    m = env["summary"]["modules"]["KV7023"]
    assert m["topics"] == 0 and m["course_map"] is False
    assert m["claude_project"]["exists"] and m["claude_project"]["stale"] is False
    assert env["summary"]["modules"]["KV7015"]["claude_project"]["exists"] is False

    (authoring / "templates" / "topic.md").write_text((authoring / "templates" / "topic.md").read_text() + "\nChanged.\n")
    _, env = bcn(capsys, "status", str(tree))
    assert env["summary"]["modules"]["KV7023"]["claude_project"]["stale"] is True
    _, env = bcn(capsys, "project", str(tree / "KV7023"))
    _, env = bcn(capsys, "status", str(tree))
    assert env["summary"]["modules"]["KV7023"]["claude_project"]["stale"] is False


def test_the_instructions_have_no_other_module_baked_in():
    text = (project.AUTHORING / "instructions.md").read_text()
    assert "KV7015" not in text
    rendered, _, _ = project.render("KV7015", 10, None)
    assert "{{" not in rendered
    assert rendered.startswith("You are working with the leader of KV7015, a postgraduate module")
