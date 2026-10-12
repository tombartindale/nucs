"""bcn docpdf: assignment briefs and unit activities as PDFs, to share with collaborators.

Prints each in-scope assignment-N.md and unit activity.md the same way bcn coursemap prints
course-map.md: plain markdown to styled HTML, via the pinned Chrome. A quiz activity
(type: quiz) is rendered as its questions and options instead, the correct answer marked and
feedback shown — a reviewable copy for a collaborator, not the LMS package bcn qti makes.

Once a document has been translated (bcn translation), its .zh.md counterpart gets its own
PDF alongside the English one. Written to <module>/build/assignment-<n>[-zh].pdf and
<module>/build/<unit>-activity[-zh].pdf; skipped if already newer than its source, unless
--force.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from .. import fsutil, tools
from ..config import load, load_theme, resolve_theme_name
from ..coursemap import module_title
from ..envelope import Diagnostic, Envelope, Fail, TopicResult
from ..progress import run as run_proc
from ..quiz import parse_quiz
from ..tree import Target
from .coursemap import document_html, quiz_html

HELP = "assignment briefs and unit activities as PDFs"


def add_args(p: argparse.ArgumentParser) -> None:
    pass


def assignment_pdf_path(root: Path, module: str, name: str) -> Path:
    """name is "assignment-1.md" or "assignment-1.zh.md"."""
    stem = name[: -len(".md")]
    if stem.endswith(".zh"):
        stem = stem[: -len(".zh")] + "-zh"
    return root / module / "build" / f"{stem}.pdf"


def activity_pdf_path(root: Path, module: str, unit: str, lang: str) -> Path:
    return root / module / "build" / f"{unit}-activity{'-zh' if lang == 'zh' else ''}.pdf"


def _front_type(lines: list[str]) -> str:
    if lines and lines[0].strip() == "---":
        for line in lines[1:30]:
            if line.strip() == "---":
                break
            if line.strip().startswith("type:"):
                return line.split(":", 1)[1].strip().strip("'\"")
    return ""


def _print(page_html: str, out: Path, work_dir: Path, tl: tools.Tools, label: str) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        page = work_dir / "doc.html"
        page.write_text(page_html, encoding="utf-8")
        with fsutil.atomic_path(out) as tmp:
            code, _, err = run_proc([tl.node or "node", str(tools.NODE_DIR / "print.mjs"), str(page), str(tmp), label],
                                    env={"CHROME_PATH": tl.chrome or ""}, cwd=str(tools.NODE_DIR), timeout=180)
            if code != 0 or not tmp.stat().st_size:
                raise Fail("RENDER_FAILED", f"Printing {label} failed (exit {code}).", data={"stderr": err[-2000:]})
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    env.diagnostics.extend(target.diagnostics)
    for m in target.modules:
        mdir = target.root / m
        try:
            cfg = load(target.root, mdir)
            theme = load_theme(target.root, resolve_theme_name(cfg, args.theme))
            tl = tools.require(cfg, "marp", "chrome")
        except Fail as f:
            env.diagnostics.append(f.diagnostic)
            continue

        def render(rel: str, lang: str, out: Path) -> None:
            src = mdir / rel
            if not src.is_file():
                return
            r = TopicResult(f"{m}/{rel}", rel)
            env.results.append(r)
            if not args.force and fsutil.is_fresh([out], [src, *theme.files(), *theme.document_media()]):
                r.skipped = True
                r.extra["pdf"] = str(out.relative_to(target.root))
                return
            lines = src.read_text(encoding="utf-8-sig").splitlines()
            front_type = _front_type(lines)
            work = mdir / "build" / f".docpdf-{out.stem}"
            try:
                if front_type == "quiz":
                    quiz = parse_quiz(src, f"{m}/{rel}")
                    page_html = quiz_html(quiz, m, quiz.title, theme=theme, lang=lang)
                else:
                    page_html = document_html(m, lines, theme=theme, title=module_title(src), lang=lang)
                _print(page_html, out, work, tl, f"{m} {rel}")
            except Fail as f:
                r.diagnostics.append(f.diagnostic)
                r.ok = False
                return
            r.artifacts.append(Envelope.artifact_for(target.root, out, "docpdf"))
            r.extra["pdf"] = str(out.relative_to(target.root))

        if target.level in ("root", "module"):
            for a in sorted(mdir.glob("assignment-*.md")):
                lang = "zh" if a.name.endswith(".zh.md") else "en"
                render(a.name, lang, assignment_pdf_path(target.root, m, a.name))

        units = sorted({t.unit for t in target.topics if t.module == m})
        if target.level == "unit":
            units = [target.rel.split("/")[1]]
        elif target.level in ("root", "module"):
            units = sorted(p.parent.name for p in mdir.glob("U*/activity.md"))
        for u in units:
            for name, lang in ((f"{u}/activity.md", "en"), (f"{u}/activity.zh.md", "zh")):
                render(name, lang, activity_pdf_path(target.root, m, u, lang))
    if not env.results and not env.diagnostics:
        env.diagnostics.append(Diagnostic("DOCPDF_NOTHING", f"No assignment or activity document was found under {target.rel}.", level="info"))
