"""bcn translation: assignment-N.md / activity.md documents riding along with topics."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from bcn.cli import main

ASSIGNMENT = """# Assignment 1: A research proposal

**What you are asked to do.** Write a short proposal.

**What you submit.** One PDF.

**How it is judged.** Clarity of the question.

**Outcomes addressed.** LO1

**Before attempting this.** Units 1 to 3.
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
    # Missing every required section but the first.
    (doc_only_module / "KV7015" / "assignment-1.md").write_text("# Assignment 1\n\n**What you are asked to do.** Do it.\n")
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


def _subtitled(tree, capsys) -> Path:
    """A topic whose English SRT has been through bcn subtitles (no video, no render): ready to
    send for translation (same construction as test_cli.test_translation_ready_without_video)."""
    from bcn.markdown import parse
    from .conftest import age
    t = tree / "KV7015/U01/T01"
    blocks, time_ = [], 0.5
    for s in parse(t / "topic.md").slides:
        for sentence in [x.strip() + "." for x in s.say_text.split(".") if x.strip()]:
            a, b = time_, time_ + len(sentence.split()) / 2.5
            blocks.append(f"{len(blocks) + 1}\n00:{int(a // 60):02d}:{a % 60:06.3f} --> 00:{int(b // 60):02d}:{b % 60:06.3f}\n{sentence}\n")
            time_ = b + 0.1
    (t / "edit").mkdir()
    (t / "edit/master.srt").write_text("\n".join(
        "\n".join(ln.replace(".", ",") if "-->" in ln else ln for ln in blk.splitlines()) + "\n" for blk in blocks))
    for f in ("topic.md", "edit/master.srt"):
        age(t / f, 30)
    for cmd in ("validate", "cues", "subtitles"):
        code, env = bcn(capsys, cmd, str(t))
        assert code == 0, (cmd, env["diagnostics"])
    return t


def _batch_names(tree, env) -> set[str]:
    """The file names in the export's zip."""
    with zipfile.ZipFile(tree / env["zip"]) as z:
        return {Path(n).name for n in z.namelist()}


def test_export_includes_the_handout_when_the_slides_are_rendered(tree, capsys):
    """The handout (each slide with what is said over it) goes in the batch for context. It is
    not a condition of sending: without a current one the topic still goes, with a warning."""
    t = _subtitled(tree, capsys)
    tid = "KV7015-U01-T01"
    code, env = bcn(capsys, "translation", str(t), "--export")
    assert code == 0, env["diagnostics"]
    assert f"{tid}.md" in _batch_names(tree, env) and f"{tid}.handout.en.pdf" not in _batch_names(tree, env)
    warned = [d for d in env["diagnostics"] if d["code"] == "XL_NO_HANDOUT"]
    assert len(warned) == 1 and warned[0]["level"] == "warn"

    (t / "build/handout.en.pdf").write_bytes(b"%PDF-1.4 stand-in")  # newer than topic.md
    code, env = bcn(capsys, "translation", str(t), "--export")
    assert code == 0, env["diagnostics"]
    assert f"{tid}.handout.en.pdf" in _batch_names(tree, env)
    assert not [d for d in env["diagnostics"] if d["code"] == "XL_NO_HANDOUT"]


def test_import_ignores_a_returned_handout(tree, tmp_path, capsys):
    """A translator who sends the whole batch back, handout included, gets no warning for it."""
    t = _subtitled(tree, capsys)
    back = tmp_path / "back"
    back.mkdir()
    (back / "KV7015-U01-T01.handout.en.pdf").write_bytes(b"%PDF-1.4 stand-in")
    code, env = bcn(capsys, "translation", str(t), "--import", str(back))
    assert "XL_UNKNOWN_FILE" not in {d["code"] for d in env["diagnostics"]}, env["diagnostics"]
