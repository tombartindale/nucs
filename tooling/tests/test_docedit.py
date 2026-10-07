"""bcn docedit: save edited text for a module document, safely, validating the whole module."""

from __future__ import annotations

import hashlib
import json

from bcn.cli import main

from .conftest import COURSE_MAP


def bcn(capsys, *args):
    code = main([*args, "--quiet"])
    return code, json.loads(capsys.readouterr().out)


def test_save_writes_and_keeps_history(tree, capsys):
    cm = tree / "KV7015" / "course-map.md"
    original = cm.read_bytes()
    sha = hashlib.sha256(original).hexdigest()
    draft = tree.parent / "draft.md"
    draft.write_text(original.decode().replace("First unit", "First unit, revised"))

    code, env = bcn(capsys, "docedit", str(tree / "KV7015"), "--doc", "course-map.md", "--from", str(draft), "--expect-sha", sha)
    assert code == 0, env["diagnostics"]
    assert env["results"][0]["written"] is True
    assert "revised" in cm.read_text()
    assert [p.read_bytes() for p in (tree / "KV7015" / ".history").iterdir()] == [original]


def test_dry_run_writes_nothing(tree, capsys):
    cm = tree / "KV7015" / "course-map.md"
    original = cm.read_bytes()
    draft = tree.parent / "draft.md"
    draft.write_text(original.decode().replace("First unit", "First unit, revised"))

    code, env = bcn(capsys, "docedit", str(tree / "KV7015"), "--doc", "course-map.md", "--from", str(draft), "--dry-run")
    assert code == 0, env["diagnostics"]
    assert env["results"][0]["written"] is False
    assert cm.read_bytes() == original


def test_sha_conflict_refuses_write(tree, capsys):
    cm = tree / "KV7015" / "course-map.md"
    original = cm.read_bytes()
    draft = tree.parent / "draft.md"
    draft.write_text(original.decode() + "\nmore\n")

    code, env = bcn(capsys, "docedit", str(tree / "KV7015"), "--doc", "course-map.md", "--from", str(draft), "--expect-sha", "0" * 64)
    assert code == 1
    assert env["diagnostics"][-1]["code"] == "EDIT_CONFLICT"
    assert cm.read_bytes() == original


def test_dry_run_reflects_cross_file_outcome_removal(tree, capsys):
    # The fixture's U01/activity.md cites LO1. Removing LO1 from the course map, without
    # saving, should show up as a DOC_OUTCOME_UNKNOWN on activity.md in the same dry-run --
    # proof the edited text is substituted into the whole module's checks, not just the one
    # file's own rules in isolation.
    edited = COURSE_MAP.replace("- **LO1** Do the first thing.\n", "")
    draft = tree.parent / "draft.md"
    draft.write_text(edited)

    # A save/dry-run "succeeds" even when the document has problems -- they are reported, not
    # a failure to save (the same convention bcn edit already uses for topic.md) -- so the
    # result to check is validation_ok and the diagnostics list, not the exit code.
    code, env = bcn(capsys, "docedit", str(tree / "KV7015"), "--doc", "course-map.md", "--from", str(draft), "--dry-run")
    assert code == 0 and env["validation_ok"] is False
    assert env["results"][0]["written"] is False
    codes_by_file = {(d["code"], d.get("file")) for d in env["diagnostics"]}
    assert ("DOC_OUTCOME_UNKNOWN", "KV7015/U01/activity.md") in codes_by_file
    # Nothing was written -- the real course-map.md still has LO1, so a plain validate run
    # (reading disk, no override) does not see the problem.
    assert "LO1" in (tree / "KV7015" / "course-map.md").read_text()


def test_rejects_unrecognized_doc_name(tree, capsys):
    draft = tree.parent / "draft.md"
    draft.write_text("hello\n")
    code, env = bcn(capsys, "docedit", str(tree / "KV7015"), "--doc", "random.md", "--from", str(draft))
    assert code == 2
    assert any(d["code"] == "USAGE" for d in env["diagnostics"])
    assert not (tree / "KV7015" / "random.md").exists()


def test_rejects_path_traversal(tree, capsys):
    draft = tree.parent / "draft.md"
    draft.write_text("hello\n")
    code, env = bcn(capsys, "docedit", str(tree / "KV7015"), "--doc", "../../etc/passwd", "--from", str(draft))
    assert code == 2
    assert any(d["code"] == "USAGE" for d in env["diagnostics"])


def test_requires_module_scope(tree, capsys):
    draft = tree.parent / "draft.md"
    draft.write_text("hello\n")
    code, env = bcn(capsys, "docedit", str(tree / "KV7015" / "U01" / "T01"), "--doc", "course-map.md", "--from", str(draft))
    assert code == 2
    assert any(d["code"] == "USAGE" for d in env["diagnostics"])


def test_creates_a_new_activity_doc(tree, capsys):
    draft = tree.parent / "draft.md"
    draft.write_text("# U02 Activity\n\n## Task\n\nDo it.\n")
    code, env = bcn(capsys, "docedit", str(tree / "KV7015"), "--doc", "U02/activity.md", "--from", str(draft))
    assert code == 0, env["diagnostics"]
    assert env["results"][0]["written"] is True
    assert (tree / "KV7015" / "U02" / "activity.md").read_text() == "# U02 Activity\n\n## Task\n\nDo it.\n"
