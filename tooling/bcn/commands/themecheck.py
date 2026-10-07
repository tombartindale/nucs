"""bcn themecheck: confirm which theme is active and that it actually resolves.

load_theme() already validates everything that matters -- the CSS declares the
right /* @theme NAME */, every font/logo/background file it names exists, a
logo isn't an EPS/AI a browser can't draw, bumper timings are sane -- so this
just calls it and reports what it found, success or the exact failure. If this
reports resolved, bcn bumpers/render will use this theme without surprises;
if it doesn't, the diagnostic here is the same one they would hit.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..config import BUNDLED_THEMES, load, load_theme, resolve_theme_name
from ..envelope import Envelope, Fail
from ..tree import find_root

HELP = "confirm which theme is active and that it resolves"


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("path", nargs="?", default=".", help="programme root")


def _find_toml(root: Path, name: str) -> Path | None:
    for base in (root / "themes", BUNDLED_THEMES):
        d = base / name
        if (d / "theme.toml").is_file():
            return d / "theme.toml"
    return None


def run(args: argparse.Namespace, env: Envelope) -> None:
    try:
        root = find_root(Path(args.path))
        env.root = root
        cfg = load(root)
    except Fail as f:
        env.diagnostics.append(f.diagnostic)
        return
    name = resolve_theme_name(cfg, args.theme)
    env.extra["theme"] = name
    # Read the raw file regardless of whether it validates, so a broken theme.toml can
    # still be inspected here -- this is the only "what's actually live on the server"
    # view the admin has, including when load_theme() itself is about to fail on it.
    toml_path = _find_toml(root, name)
    env.extra["toml_text"] = toml_path.read_text() if toml_path else None
    try:
        theme = load_theme(root, name)
    except Fail as f:
        env.extra["resolved"] = False
        env.diagnostics.append(f.diagnostic)
        return
    custom_dir = root / "themes" / name
    env.extra.update({
        "resolved": True,
        "dir": str(theme.dir),
        "custom": theme.dir == custom_dir,
        "css": theme.css.name,
        "files": [str(p.relative_to(theme.dir)) for p in theme.files()],
        "bumper_logo": (theme.bumper or {}).get("logo") or None,
        "bumper_background_video": (theme.bumper or {}).get("background_video") or None,
        "document_logo": (theme.document or {}).get("logo") or None,
        "font_faces": [f.get("file") for f in (theme.font_faces or []) if f.get("file")],
    })
