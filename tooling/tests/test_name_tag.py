"""The speaker's name tag compose lays over the start of the body: where the name comes from,
how the theme times it, and that it really appears and goes in the composed video."""

from __future__ import annotations

import subprocess

import pytest

from bcn import tools
from bcn.commands.compose import EncodeSpec, NameTag, filter_graph, name_tag_box
from bcn.config import load, load_theme
from bcn.coursemap import load_course_map, speaker_for
from bcn.envelope import Fail
from bcn.tree import Topic

from .test_bumpers import _isolated_theme
from .test_cli import bcn
from .test_compose import _build_up_to_cues

SPEAKER = ("**Speaker.** Dr Jane Example, Associate Professor, Example University\n\n"
           "**Speaker (zh).** 简·示例博士\n\n")


def _with_speaker(tree, lines: str = SPEAKER, unit_lines: str = "") -> None:
    """The fixture's course map with Speaker lines above the first unit (and optionally inside U01)."""
    cm = tree / "KV7015" / "course-map.md"
    text = cm.read_text().replace("## Learning outcomes", lines + "## Learning outcomes", 1)
    cm.write_text(text.replace("## U01 First unit\n", "## U01 First unit\n\n" + unit_lines, 1))


def test_speaker_from_course_map(tree):
    assert speaker_for(load_course_map(tree / "KV7015"), "U01", "en") is None
    _with_speaker(tree)
    cm = load_course_map(tree / "KV7015")
    # The first comma separates the name from the role; the rest of the role keeps its commas.
    assert speaker_for(cm, "U01", "en") == ("Dr Jane Example", "Associate Professor, Example University")
    # Mandarin: its own line; a line with no comma is a name alone.
    assert speaker_for(cm, "U01", "zh") == ("简·示例博士", "")
    assert not cm.diagnostics


def test_unit_speaker_overrides_module(tree):
    _with_speaker(tree, unit_lines="**Speaker.** Prof Guest，Visiting Fellow\n\n")
    cm = load_course_map(tree / "KV7015")
    assert speaker_for(cm, "U01", "en") == ("Prof Guest", "Visiting Fellow")  # a full-width comma splits too
    # The unit's guest wins over the module's Mandarin line: the video shows who is speaking.
    assert speaker_for(cm, "U01", "zh") == ("Prof Guest", "Visiting Fellow")
    assert speaker_for(cm, "U02", "en") == ("Dr Jane Example", "Associate Professor, Example University")


def test_theme_name_tag_timing_validated(tree):
    toml = _isolated_theme(tree)
    base = toml.read_text()
    assert load_theme(tree, "default").name_tag["hold_seconds"] == 5.0
    toml.write_text(base.replace("hold_seconds = 5.0 ", "hold_seconds = 0.5 "))
    with pytest.raises(Fail, match="hold_seconds"):
        load_theme(tree, "default")


def test_filter_graph_fades_tag_after_logo(tree):
    theme = load_theme(tree, "default")
    es = EncodeSpec(1920, 1080, 25, "", "h264", "aac", 18, "slow", ("8M", "192k"))
    c = {**load(tree)["compose"], "layout": "side_by_side"}
    x, y, w, h, _ = name_tag_box(c, es, theme)
    assert x >= 1920 * (1 - theme.safe_right) and y + h <= 1080 - theme.safe_bottom  # in the presenter's half, above the subtitles
    assert 1920 - (x + w) < 1080 * 0.05  # its area reaches the frame's right edge, less the margin
    tag = NameTag("3:v", x, y, 1.0, 5.0, 0.5)
    g = filter_graph(c, es, 60.0, None, "Helvetica", None, logo_input="2:v", tag=tag)
    assert "fade=t=in:st=1.000:d=0.500:alpha=1" in g and "fade=t=out:st=5.500:d=0.500:alpha=1" in g
    assert g.index("[2:v]overlay") < g.index("[tag]overlay")  # the tag is drawn over the logo
    plain = filter_graph(c, es, 60.0, None, "Helvetica", None)
    assert "[tag]" not in plain and "[comp]" in plain


def _region_luma(video, t: float, box) -> float:
    x, y, w, h = box
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1", "-vf",
                          f"crop={w}:{h}:{x}:{y},signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-",
                          "-f", "null", "-"], capture_output=True, text=True, check=True).stdout
    return float(out.split("YAVG=")[1].split()[0])


def test_compose_draws_tag_for_its_window(tree, capsys):
    t = Topic(tree, "KV7015", "U01", "T01")
    _build_up_to_cues(tree, capsys, t)
    _isolated_theme(tree)

    code, env = bcn(capsys, "compose", str(t.dir), "--draft", "--no-bumpers")
    assert code == 0 and env["results"][0]["name_tag"] is None
    assert "COMPOSE_NO_SPEAKER" in [d["code"] for d in env["diagnostics"]]

    _with_speaker(tree)
    code, env = bcn(capsys, "compose", str(t.dir), "--draft", "--no-bumpers")  # the course map is an input: not skipped
    assert code == 0, env["diagnostics"]
    assert env["results"][0]["name_tag"] == "Dr Jane Example"

    cfg = load(tree)
    c = cfg["compose"]
    es = EncodeSpec(c["draft_width"], c["draft_height"], c["draft_fps"], "", "h264", "aac", 30, "veryfast", None)
    x, y, w, h, font = name_tag_box(c, es, load_theme(tree, "default"))
    # The strip of padding just inside the box's right edge (align = "right"), past the accent
    # bar: box colour, no text.
    bar, strip = max(3, round(font * 0.18)), max(2, round(font * 0.35 * 1.6) - 2)
    box = (x + w - bar - 1 - strip, y + h // 3, strip, h // 3)
    before, during, after = (_region_luma(t.draft("en"), s, box) for s in (0.3, 3.0, 7.5))
    assert abs(before - after) < 3, (before, after)
    assert abs(during - before) > 15, (before, during)
    assert tools.locate(cfg).chrome  # rendered with the pinned Chrome, like the logo
