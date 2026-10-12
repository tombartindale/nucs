"""bcn translation: assignment-N.md / activity.md documents riding along with topics."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from bcn.cli import main

ASSIGNMENT = """# Assignment 1: A research proposal

## Brief

Write a short proposal.

## Outcomes

LO1

## Assessment criteria

- Clarity of the question
"""

COURSE_MAP = """# KV7015 Test module

## Learning outcomes

- **LO1** Do the first thing.
"""

QUIZ_EN = """---
unit: KV7015-U01
type: quiz
lang: en
---

# Unit 1 — Check your understanding

## Question 1

What is the answer?

- (a) Wrong → No.
- (b) Right ✔ → Yes.
"""


def bcn(capsys, *args):
    code = main([*args, "--quiet"])
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def doc_only_module(tmp_path: Path) -> Path:
    """A module with course-map.md and a ready assignment-1.md, but no units/topics at
    all — isolates assessment-document export from topic readiness, which needs a real
    edited video and SRT to satisfy."""
    root = tmp_path / "prog"
    mdir = root / "KV7015"
    mdir.mkdir(parents=True)
    (root / "programme.toml").write_text('[programme]\nname = "test"\n')
    (mdir / "course-map.md").write_text(COURSE_MAP)
    (mdir / "assignment-1.md").write_text(ASSIGNMENT)
    return root


def test_export_includes_a_ready_assignment_document(doc_only_module, capsys):
    code, env = bcn(capsys, "translation", str(doc_only_module / "KV7015"), "--export")
    assert code == 0, env["diagnostics"]
    assert env["batch"]
    with zipfile.ZipFile(doc_only_module / env["zip"]) as z:
        names = set(z.namelist())
        manifest = json.loads(z.read(f"{env['batch']}/manifest.json"))
    assert f"{env['batch']}/KV7015-assignment-1.md" in names
    assert manifest["documents"] == ["KV7015/assignment-1.md"]
    assert any(f["name"] == "KV7015-assignment-1.md" for f in manifest["files"])
    assert any(d["code"] == "XL_EXPORTED" and d["file"] == "KV7015/assignment-1.md" for d in env["diagnostics"])


def test_export_blocks_whole_batch_when_a_document_is_not_ready(doc_only_module, capsys):
    # Missing the required "Outcomes"/"Assessment criteria" headings.
    (doc_only_module / "KV7015" / "assignment-1.md").write_text("# Assignment 1\n\n## Brief\n\nDo it.\n")
    code, env = bcn(capsys, "translation", str(doc_only_module / "KV7015"), "--export")
    assert code == 1  # XL_NOT_READY is an error-level diagnostic
    assert any(d["code"] == "XL_NOT_READY" for d in env["diagnostics"])
    assert "zip" not in env


def test_export_nothing_in_scope_is_a_quiet_no_op(doc_only_module, capsys):
    (doc_only_module / "KV7015" / "assignment-1.md").unlink()
    code, env = bcn(capsys, "translation", str(doc_only_module / "KV7015"), "--export")
    assert code == 0
    assert "zip" not in env


def test_import_places_assignment_and_activity_translations(doc_only_module, tmp_path, capsys):
    unit_dir = doc_only_module / "KV7015" / "U01"
    unit_dir.mkdir()
    (unit_dir / "activity.md").write_text(QUIZ_EN)
    zpath = tmp_path / "returned.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("KV7015-assignment-1.zh.md", "# 作业一：研究计划\n\n## Brief\n\n写一份简短的计划。\n\n## Outcomes\n\nLO1\n\n## Assessment criteria\n\n- 问题的清晰度\n")
        z.writestr("KV7015-U01-activity.zh.md", "---\nunit: KV7015-U01\ntype: quiz\nlang: zh\n---\n\n# 第一单元 — 检查你的理解\n\n## 问题一\n\n答案是什么？\n\n- (a) 错 → 不对。\n- (b) 对 ✔ → 是的。\n")
    code, env = bcn(capsys, "translation", str(doc_only_module / "KV7015"), "--import", str(zpath))
    assert code == 0, env["diagnostics"]
    assert (doc_only_module / "KV7015" / "assignment-1.zh.md").is_file()
    assert (unit_dir / "activity.zh.md").is_file()
    paths = {r["topic"] for r in env["results"]}
    assert "KV7015/assignment-1.zh.md" in paths and "KV7015/U01/activity.zh.md" in paths
    assert env.get("return_to_translator") == []


def test_import_flags_quiz_that_changes_the_correct_answer(doc_only_module, tmp_path, capsys):
    unit_dir = doc_only_module / "KV7015" / "U01"
    unit_dir.mkdir()
    (unit_dir / "activity.md").write_text(QUIZ_EN)
    zpath = tmp_path / "returned.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        # The translation flips which option is correct: (a) is now marked right, not (b).
        z.writestr("KV7015-U01-activity.zh.md",
                   "---\nunit: KV7015-U01\ntype: quiz\nlang: zh\n---\n\n# 第一单元\n\n## 问题一\n\n答案是什么？\n\n"
                   "- (a) 对 ✔ → 是的。\n- (b) 错 → 不对。\n")
    code, env = bcn(capsys, "translation", str(doc_only_module / "KV7015"), "--import", str(zpath))
    assert code == 1  # QUIZ_ZH_PARITY is an error-level diagnostic
    codes = [x["code"] for x in env["return_to_translator"]]
    assert "QUIZ_ZH_PARITY" in codes
