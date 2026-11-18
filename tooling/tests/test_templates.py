"""The Claude Project templates (authoring/templates/) must pass the checks bcn applies to what
the Projects produce from them, so the format the module leaders' assistant is told to use
and the format NUCS accepts can never drift apart."""

from __future__ import annotations

from pathlib import Path

from bcn.config import DEFAULTS
from bcn.coursemap import validate_doc

TEMPLATES = Path(__file__).resolve().parents[2] / "authoring" / "templates"


def codes(diags):
    return [d.code for d in diags]


def test_the_assignment_brief_template_has_every_required_section():
    path = TEMPLATES / "assignment-brief.md"
    diags = validate_doc(path, "KV7015/assignment-1.md", DEFAULTS["documents"]["assignment_headings"], {"LO1": "", "LO2": ""})
    assert "DOC_HEADING_MISSING" not in codes(diags), [d.message for d in diags]


def test_a_brief_missing_a_section_is_caught_whether_written_as_labels_or_headings(tmp_path):
    required = DEFAULTS["documents"]["assignment_headings"]
    labels = tmp_path / "labels.md"
    labels.write_text("# Assignment 1\n\n**What you are asked to do.** Do it.\n\n**What you submit:** A PDF.\n")
    missing = [d.message for d in validate_doc(labels, "a.md", required, {}) if d.code == "DOC_HEADING_MISSING"]
    assert len(missing) == len(required) - 2
    assert not any("What you submit" in m for m in missing)

    headings = tmp_path / "headings.md"
    headings.write_text("# Assignment 1\n\n" + "".join(f"## {h}\n\nText.\n\n" for h in required))
    assert "DOC_HEADING_MISSING" not in codes(validate_doc(headings, "a.md", required, {}))

    # Bold text inside a paragraph is not a section label.
    inline = tmp_path / "inline.md"
    inline.write_text("# Assignment 1\n\nYou will see **What you submit** later.\n")
    assert len([d for d in validate_doc(inline, "a.md", required, {}) if d.code == "DOC_HEADING_MISSING"]) == len(required)


def test_the_course_map_template_parses_with_its_extra_columns(tmp_path):
    from bcn.coursemap import load_course_map

    module = tmp_path / "KV7015"
    module.mkdir()
    (module / "course-map.md").write_text((TEMPLATES / "course-map.md").read_text())
    cm = load_course_map(module)
    assert [d.message for d in cm.diagnostics] == []
    t1, t2 = cm.topics[:2]
    assert (t1.unit, t1.code, t1.title, t1.minutes, t1.outcomes) == ("U01", "T01", "Turning a topic into a question", 10, ["LO1"])
    assert t2.outcomes == ["LO1", "LO2"]
    assert set(cm.outcomes) == {"LO1", "LO2"}
    assert cm.readings["U01"].startswith("Course textbook")


def test_the_topic_template_names_images_the_way_intake_does():
    # authoring/templates/topic.md, the prompt, the image skill and bcn intake's extraction
    # all write assets/fig-<n>.<ext>: unpadded, no leading ./ or /.
    import re

    refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", (TEMPLATES / "topic.md").read_text())
    assert refs and all(re.fullmatch(r"assets/fig-[1-9]\d*\.[a-z]+", r) for r in refs), refs
