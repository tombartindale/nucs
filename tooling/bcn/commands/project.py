"""bcn project: the Claude Project pack for a module, and creating a new module.

Each module's content is written by its leader in conversation with a Claude Project: one
Project per module, holding the interview instructions and a template for every document it
produces. The canonical copies live in the repository under authoring/ (instructions.md, with
{{module}}-style placeholders, and templates/). This renders them for one module into

    <module>/build/claude-project/instructions.md   paste into the Project's Instructions box
    <module>/build/claude-project/knowledge/*.md     upload as Project knowledge
    <module>/build/claude-project/SETUP.md           the producer's checklist
    <module>/build/claude-project/pack.json          what was issued: version, units, title
    <module>/build/claude-project.zip                the knowledge files, to upload in one go

`version` is a short hash of the rendered instructions and templates, so a Project set up
from an older pack can be told apart (status reports it as stale once authoring/ changes).

    bcn project <root> --new KV7023 [--title "..."] [--units 10]
        creates the module folder (refusing one that exists), then renders its pack. No
        course-map.md is created: that is what the Project produces in stage one.
    bcn project <module|root>
        (re)renders the pack for each module, keeping the units/title it was issued with.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import fsutil
from ..coursemap import load_course_map, module_title
from ..envelope import Envelope, Fail, TopicResult
from ..tree import MODULE_RE, Target, find_root

HELP = "the Claude Project pack (instructions and templates) for a module; --new creates a module"

AUTHORING = Path(os.environ.get("BCN_AUTHORING") or Path(__file__).resolve().parents[3] / "authoring")
PACK_DIR = "build/claude-project"
PACK_ZIP = "build/claude-project.zip"
DEFAULT_UNITS = 10
TEMPLATE_MODULE = "KV7015"  # the module code the templates' worked examples are written for
TITLE_RE = re.compile(r"^[^\x00-\x1f]{1,120}$")

# What the producer adds to the Project beside the templates; NUCS has no copy of these.
PRODUCER_DOCS = [
    "The module descriptor",
    "The validated learning outcomes",
    "The indicative reading list",
]


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--new", metavar="MODULE", help="create this module (e.g. KV7023) under the programme root first")
    p.add_argument("--title", help="the module's title, named in the instructions")
    p.add_argument("--units", type=int, help=f"how many units the module has (default {DEFAULT_UNITS}, or the course map's count)")


def prepare(args: argparse.Namespace) -> None:
    """--new: create the module folder before the target is resolved."""
    if args.title and not TITLE_RE.fullmatch(args.title.strip()):
        raise Fail("USAGE", "--title must be one line of at most 120 characters.")
    if args.units is not None and not 1 <= args.units <= 30:
        raise Fail("USAGE", "--units must be between 1 and 30.")
    if not args.new:
        return
    code = args.new.strip()
    if not MODULE_RE.fullmatch(code):
        raise Fail("MODULE_CODE_INVALID", f"'{code}' is not a module code: two capital letters and four digits, e.g. KV7023.")
    # The path is the programme root, or the new module's own (not yet existing) folder. The
    # UI uses the latter so its job targets just that module: a job on the whole programme
    # waits for every other job to finish first.
    path = Path(args.path).resolve()
    root = find_root(path).resolve()
    if path != root and path != root / code:
        raise Fail("USAGE", f"--new takes the programme root, or the new module's own path ({code}), as its path.")
    existing = {p.name.upper(): p.name for p in root.iterdir() if p.is_dir()}
    if code.upper() in existing:
        raise Fail("MODULE_EXISTS", f"{existing[code.upper()]} already exists.", file=existing[code.upper()])
    (root / code).mkdir()
    args.new = code


def _templates() -> list[Path]:
    tdir = AUTHORING / "templates"
    files = sorted(tdir.glob("*.md")) if tdir.is_dir() else []
    if not (AUTHORING / "instructions.md").is_file() or not files:
        raise Fail("PROJECT_SOURCE_MISSING", f"The Claude Project instructions and templates are not installed ({AUTHORING}).")
    return files


def render(module: str, units: int, title: str | None) -> tuple[str, dict[str, str], str]:
    """The instructions, the knowledge files by name, and the pack version, for one module."""
    name = f"{module} ({title})" if title else module
    text = (AUTHORING / "instructions.md").read_text(encoding="utf-8")
    instructions = (text.replace("{{module_name}}", name).replace("{{units}}", str(units))
                    .replace("{{module}}", module))
    knowledge = {p.name: p.read_text(encoding="utf-8").replace(TEMPLATE_MODULE, module) for p in _templates()}
    h = hashlib.sha256(instructions.encode())
    for n in sorted(knowledge):
        h.update(b"\0" + n.encode() + b"\0" + knowledge[n].encode())
    return instructions, knowledge, h.hexdigest()[:10]


def setup_text(module: str, title: str | None, version: str, files: list[str]) -> str:
    project = f"{module} – {title}" if title else module
    lines = [
        f"# Setting up the Claude Project for {module}",
        "",
        f"Pack version `{version}`.",
        "",
        f"1. In Claude, create a Project called **{project}**.",
        "2. Paste `instructions.md` into the Project's **Instructions** box (replace anything already there).",
        "3. Add these files to the Project's knowledge (they are in the knowledge zip):",
        *[f"   - [ ] `{f}`" for f in files],
        "4. Add these from your own copies; NUCS does not have them:",
        *[f"   - [ ] {d}" for d in PRODUCER_DOCS],
        "5. Share the Project with the module leader.",
        "",
        "The module leader then starts a chat in the Project. Its first output is the course map,",
        "which comes back to you at gate 1.",
        "",
    ]
    return "\n".join(lines)


def _units_of(root: Path, module: str) -> int | None:
    try:
        n = len(load_course_map(root / module).units)
    except Exception:  # noqa: BLE001 -- a broken map just means no count from it
        return None
    return n or None


def build(root: Path, module: str, units: int | None = None, title: str | None = None) -> dict[str, Any]:
    """Render and write the pack. units/title not given are kept from the last pack, then
    taken from the course map, then defaulted."""
    mdir = root / module
    prev = fsutil.read_json(mdir / PACK_DIR / "pack.json") or {}
    units = units or prev.get("units") or _units_of(root, module) or DEFAULT_UNITS
    title = (title.strip() if title else None) or prev.get("title") or module_title(mdir / "course-map.md") or None
    instructions, knowledge, version = render(module, units, title)
    out = mdir / PACK_DIR
    (out / "knowledge").mkdir(parents=True, exist_ok=True)
    for stale in (out / "knowledge").glob("*.md"):
        if stale.name not in knowledge:
            stale.unlink()
    fsutil.write_text(out / "instructions.md", instructions)
    for n, text in knowledge.items():
        fsutil.write_text(out / "knowledge" / n, text)
    files = sorted(knowledge)
    fsutil.write_text(out / "SETUP.md", setup_text(module, title, version, files))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n in files:
            z.writestr(n, knowledge[n])
    fsutil.write_bytes(mdir / PACK_ZIP, buf.getvalue())
    info = {
        "version": version, "units": units, "title": title, "files": files, "producer_docs": PRODUCER_DOCS,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    fsutil.write_json(out / "pack.json", info)
    return info


def status_of(root: Path, module: str) -> dict[str, Any] | None:
    """For bcn status: the module's pack, and whether authoring/ has changed since it was made."""
    mdir = root / module
    info = fsutil.read_json(mdir / PACK_DIR / "pack.json")
    if not info:
        return {"exists": False, "path": f"{module}/{PACK_DIR}", "zip": f"{module}/{PACK_ZIP}",
                "version": None, "current": None, "stale": None, "generated": None, "units": None, "title": None}
    try:
        current = render(module, info.get("units") or DEFAULT_UNITS, info.get("title"))[2]
    except Fail:
        current = None
    return {"exists": True, "path": f"{module}/{PACK_DIR}", "zip": f"{module}/{PACK_ZIP}",
            "version": info.get("version"), "current": current,
            "stale": (current is not None and current != info.get("version")),
            "generated": info.get("generated"), "units": info.get("units"), "title": info.get("title")}


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    modules = [args.new] if args.new else target.modules
    if not modules:
        raise Fail("USAGE", "There are no modules here to make a Claude Project pack for.")
    if args.new:
        env.extra["created"] = args.new
    for m in modules:
        r = TopicResult(m, m)
        try:
            info = build(target.root, m, args.units, args.title)
        except Fail as f:
            r.ok = False
            r.diagnostics.append(f.diagnostic)
        else:
            r.extra.update(info)
            r.extra["path"] = f"{m}/{PACK_DIR}"
            r.extra["zip"] = f"{m}/{PACK_ZIP}"
        env.results.append(r)

