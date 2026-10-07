"""bcn themecheck: confirm which theme is active and that it actually resolves."""

from __future__ import annotations

import json

from bcn.cli import main


def bcn(capsys, *args):
    code = main([*args, "--quiet"])
    return code, json.loads(capsys.readouterr().out)


def test_resolves_the_bundled_default_theme(tree, capsys):
    code, env = bcn(capsys, "themecheck", str(tree))
    assert code == 0, env["diagnostics"]
    assert env["theme"] == "default"
    assert env["resolved"] is True
    assert env["custom"] is False
    assert "theme.toml" in [f for f in env["files"] if f == "theme.toml"] or any(env["files"])


def test_resolves_an_uploaded_custom_theme(tree, capsys):
    custom = tree / "themes" / "custom"
    custom.mkdir(parents=True)
    (custom / "theme.toml").write_text(
        'css = "x.css"\n[slide]\nwidth = 1920\nheight = 1080\n[safe_area]\nbottom = 0\n[fonts]\nen = ["Arial"]\nzh = ["Arial"]\n')
    (custom / "x.css").write_text("/* @theme custom */\n")
    (tree / "programme.toml").write_text('[programme]\nname = "test"\ntheme = "custom"\n')

    code, env = bcn(capsys, "themecheck", str(tree))
    assert code == 0, env["diagnostics"]
    assert env["theme"] == "custom"
    assert env["resolved"] is True
    assert env["custom"] is True
    assert set(env["files"]) == {"theme.toml", "x.css"}


def test_reports_the_exact_failure_for_an_unknown_theme(tree, capsys):
    code, env = bcn(capsys, "themecheck", str(tree), "--theme", "does-not-exist")
    assert code == 1
    assert env["resolved"] is False
    assert env["diagnostics"][0]["code"] == "RENDER_THEME"


def test_reports_the_exact_failure_for_a_broken_custom_theme(tree, capsys):
    # A theme that exists but is missing a declared font file: load_theme()'s own validation
    # should surface here exactly as bcn bumpers/render would hit it.
    custom = tree / "themes" / "custom"
    custom.mkdir(parents=True)
    (custom / "theme.toml").write_text(
        'css = "x.css"\n[slide]\nwidth = 1920\nheight = 1080\n[safe_area]\nbottom = 0\n[fonts]\nen = ["Face"]\nzh = ["Face"]\n'
        '[[font_face]]\nfamily = "Face"\nfile = "fonts/missing.otf"\nweight = 400\n')
    (custom / "x.css").write_text("/* @theme custom */\n")

    code, env = bcn(capsys, "themecheck", str(tree), "--theme", "custom")
    assert code == 1
    assert env["resolved"] is False
    assert env["diagnostics"][0]["code"] == "RENDER_THEME"
    assert "missing.otf" in env["diagnostics"][0]["message"]
