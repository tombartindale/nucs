"""bcn render: the slide PNGs, and the second, full-bleed set bcn compose uses for every
arrangement except side_by_side (see tooling/bcn/commands/render.py's marp_source/render_topic)."""

from __future__ import annotations

from bcn.commands.render import marp_source
from bcn.config import load_theme
from bcn.markdown import parse
from bcn.tree import Topic

from .test_bumpers import _needs_tools
from .test_cli import bcn


def test_marp_source_full_bleed_removes_safe_right_padding(tree):
    """The mechanism itself, without a slow end-to-end render: the default theme reserves
    half the frame (safe_area.right = 0.5); passing safe_right=1.0 must zero that padding."""
    theme = load_theme(tree, "default")
    p = parse(tree / "KV7015/U01/T01/topic.md")
    reserved = marp_source(p, theme, "en")
    full = marp_source(p, theme, "en", safe_right=1.0)
    safe_right_px = round(theme.width * (1 - theme.safe_right))
    assert f"--bcn-safe-right: {safe_right_px}px;" in reserved and safe_right_px > 0
    assert "--bcn-safe-right: 0px;" in full
    assert reserved != full


def test_render_produces_reserved_and_full_bleed_slide_sets(tree, capsys):
    _needs_tools(tree)
    t = Topic(tree, "KV7015", "U01", "T01")
    bcn(capsys, "validate", str(t.dir))
    code, env = bcn(capsys, "render", str(t.dir))
    assert code == 0, env["diagnostics"]

    n = len(parse(t.src("en")).slides)
    reserved = t.slide_pngs("en")
    full = t.slide_pngs("en", full_bleed=True)
    assert len(reserved) == n and len(full) == n
    assert t.slides_dir("en", full_bleed=True) == t.slides_dir("en").parent / "en-full"

    for i in range(1, n + 1):
        assert (t.slides_dir("en") / t.slide_name(i, "en")).is_file()
        assert (t.slides_dir("en", full_bleed=True) / t.slide_name(i, "en")).is_file()

    # The PDF is a human-review artefact of the reserved render only; the full-bleed pass
    # never produces one.
    assert t.deck_pdf("en").is_file()

    # Re-running without changes skips both passes (render is a single try_skip step).
    code, env = bcn(capsys, "render", str(t.dir))
    assert code == 0 and env["results"][0]["skipped"] is True
