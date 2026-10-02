"""bcn readinglist: pulling each unit's Reading paragraph out of course-map.md."""

from __future__ import annotations

import json
from pathlib import Path

from bcn.cli import main
from bcn.coursemap import load_course_map, split_citations

MAP_WITH_READING = """# KV7016 — Course map

## Unit 1 — How AI depends on data

**Overview.** Prose the parser should ignore.

**Reading.** Sambasivan et al. (2021), Data Cascades in High-Stakes AI. An accessible introduction is still to be identified.

| Topic | Title | Minutes | Outcomes |
| --- | --- | --- | --- |
| U01-T01 | What an AI model does | 12 | LO2 |

## Unit 2 — The organisational data lifecycle

**Overview.** Another unit, with a single-sentence reading and none of the other labels.

**Reading.** UK Government Data Quality Framework, data lifecycle section.

| Topic | Title | Minutes | Outcomes |
| --- | --- | --- | --- |
| U02-T01 | Where data comes from | 10 | LO1 |

## Unit 3 — No reading at all

| Topic | Title | Minutes | Outcomes |
| --- | --- | --- | --- |
| U03-T01 | A topic with nothing to read | 10 | LO1 |

## Outcome coverage

| Outcome | Covered by |
| --- | --- |
| LO1 Role of data | U02-T01, U03-T01 |
| LO2 Data quality | U01-T01 |
"""


def bcn(capsys, *args):
    code = main([*args, "--quiet"])
    return code, json.loads(capsys.readouterr().out)


def _module(tmp_path: Path, text: str) -> Path:
    root = tmp_path / "prog"
    d = root / "KV7016"
    d.mkdir(parents=True)
    (root / "programme.toml").write_text('[programme]\nname = "test"\n')
    (d / "course-map.md").write_text(text)
    return root


def test_load_course_map_extracts_reading_per_unit(tmp_path):
    d = tmp_path / "KV7016"
    d.mkdir()
    (d / "course-map.md").write_text(MAP_WITH_READING)
    cm = load_course_map(d)
    assert cm.readings == {
        "U01": "Sambasivan et al. (2021), Data Cascades in High-Stakes AI. An accessible introduction is still to be identified.",
        "U02": "UK Government Data Quality Framework, data lifecycle section.",
    }
    assert "U03" not in cm.readings


def test_split_citations_does_not_break_on_et_al():
    citations = split_citations("Sambasivan et al. (2021), Data Cascades in High-Stakes AI. An accessible introduction is still to be identified.")
    assert citations == [
        "Sambasivan et al. (2021), Data Cascades in High-Stakes AI.",
        "An accessible introduction is still to be identified.",
    ]


def test_split_citations_single_sentence_stays_one_entry():
    assert split_citations("UK Government Data Quality Framework, data lifecycle section.") == \
        ["UK Government Data Quality Framework, data lifecycle section."]


def test_readinglist_command_writes_expected_file(tmp_path, capsys):
    root = _module(tmp_path, MAP_WITH_READING)
    code, env = bcn(capsys, "readinglist", str(root / "KV7016"))
    assert code == 0, env["diagnostics"]
    result = env["results"][0]
    assert result["units"] == 2 and result["entries"] == 3
    out = (root / "KV7016" / "build" / "reading-list.md").read_text()
    assert out == (
        "# KV7016 — Reading list\n\n"
        "## U01 — How AI depends on data\n\n"
        "- Sambasivan et al. (2021), Data Cascades in High-Stakes AI.\n"
        "- An accessible introduction is still to be identified.\n\n"
        "## U02 — The organisational data lifecycle\n\n"
        "- UK Government Data Quality Framework, data lifecycle section.\n"
    )


def test_readinglist_skips_when_fresh(tmp_path, capsys):
    root = _module(tmp_path, MAP_WITH_READING)
    code, env = bcn(capsys, "readinglist", str(root / "KV7016"))
    assert code == 0 and not env["results"][0]["skipped"]
    code, env = bcn(capsys, "readinglist", str(root / "KV7016"))
    assert code == 0 and env["results"][0]["skipped"]


def test_readinglist_empty_map_reports_info_diagnostic(tmp_path, capsys):
    root = _module(tmp_path, "# KV7016 — Course map\n\n## Unit 1 — No reading\n\n| Topic | Title | Minutes | Outcomes |\n| --- | --- | --- | --- |\n| U01-T01 | A topic | 10 | LO1 |\n")
    code, env = bcn(capsys, "readinglist", str(root / "KV7016"))
    assert code == 0  # an info diagnostic does not fail the command
    assert any(d["code"] == "READINGLIST_EMPTY" for d in env["diagnostics"])


def test_readinglist_missing_course_map(tmp_path, capsys):
    root = tmp_path / "prog"
    (root / "KV7016").mkdir(parents=True)
    (root / "programme.toml").write_text('[programme]\nname = "test"\n')
    code, env = bcn(capsys, "readinglist", str(root / "KV7016"))
    assert code == 1
    assert env["results"][0]["ok"] is False
    assert any(d["code"] == "DOC_MISSING" for d in env["diagnostics"])
