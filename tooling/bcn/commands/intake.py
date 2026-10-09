"""bcn intake: place pasted topic markdown into the tree, then validate it.

Identify, check, place, validate. A paste may hold a whole unit; it is split on
topic front matter. A topic not in the course map is refused, and by default an
existing topic.md that differs is never overwritten: the diff is returned instead.
--replace overwrites it anyway, for a re-send of corrected content.

--from also accepts a folder or a .zip of real topic.md files (e.g. a batch handed
over by a content creator), not just one pasted text file: every .md file found is
read and concatenated, each already carrying its own topic_id front matter, then
split and placed exactly as a single paste would be.

A topic's images come in with it, matched by filename against whatever each topic's
own slides reference (not by blindly copying everything in the source, nor by
assuming one fixed layout): first an assets/ folder sitting right beside that
topic's own .md file (the pipeline's own nested layout), then falling back to any
other file of that name found anywhere in the source, including under --assets, a
second folder or .zip supplied separately for a batch that ships its images apart
from the (often flat-named) .md files rather than nested beside them. Without this,
an image the script references would validate as missing even though it was right
there in the same upload.
"""

from __future__ import annotations

import argparse
import difflib
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from .. import fsutil
from ..config import load
from ..envelope import Diagnostic, Envelope, Fail, TopicResult
from ..markdown import parse
from ..rules import validate_topic
from ..state import module_context
from ..tree import TOPIC_ID_RE, Target, Topic, is_noise

HELP = "place pasted topic markdown into the tree and validate it"
FENCE = re.compile(r"^\s*(```|~~~)[\w-]*\s*$")


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--from", dest="source", required=True, help="file, folder or .zip holding the topic markdown")
    p.add_argument("--assets", dest="assets_source", help="optional extra folder or .zip of images, matched to each topic by filename")
    p.add_argument("--replace", action="store_true", help="overwrite a topic.md that already exists and differs, instead of refusing")
    p.add_argument("--dry-run", action="store_true", help="check and report, write nothing")


def _extract(source: str, root: Path) -> tuple[Path, Path | None]:
    """A folder is used directly (nothing to clean up); a .zip is extracted, preserving
    its own directory structure, into a fresh temp dir under root, which the caller must
    remove when done. Flattening every member to its bare filename (the previous
    behaviour) let two files that happened to share a name silently overwrite one another
    during extraction, before either was even read."""
    src = Path(source)
    if not src.exists():
        raise Fail("FS_MISSING", f"{source} does not exist.")
    if src.is_dir():
        return src, None
    if src.suffix.lower() != ".zip":
        raise Fail("USAGE", f"{source} must be a folder or a .zip.")
    tmp = Path(tempfile.mkdtemp(prefix=".intake-", dir=root))
    with zipfile.ZipFile(src) as z:
        for member in z.namelist():
            if member.endswith("/") or is_noise(Path(member).name) or "__MACOSX" in member:
                continue
            dest = tmp / member
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(z.read(member))
    return tmp, tmp


def _flat_assets(base: Path) -> dict[str, list[tuple[Path, bytes]]]:
    """Every non-.md file anywhere under base, read into memory now (base may be a temp
    dir about to be removed) and keyed by filename -- the fallback pool for a flat batch
    that ships a shared assets folder instead of nesting one beside each topic."""
    pool: dict[str, list[tuple[Path, bytes]]] = {}
    for f in base.rglob("*"):
        if f.is_file() and f.suffix.lower() != ".md" and not is_noise(f.name) and "__MACOSX" not in f.parts:
            pool.setdefault(f.name, []).append((f.relative_to(base), f.read_bytes()))
    return pool


def _read_source(source: str, root: Path) -> tuple[str, dict[str, dict[str, bytes]], dict[str, list[tuple[Path, bytes]]]]:
    """One file is read as-is (today's paste path; no assets pool, since there is no
    sibling folder to find them in). A folder or .zip has every .md file inside read and
    concatenated, so a batch of real topic.md files intakes the same way a single
    multi-topic paste does.

    Returns (text, sibling_assets, flat_assets): sibling_assets maps a topic_id to the
    contents of an assets/ folder that sat right beside that topic's own .md file (read
    eagerly, since the source may be a temp dir about to be removed); flat_assets is every
    other non-.md file found anywhere in the source, keyed by filename, for a batch that
    does not nest its images per topic."""
    src = Path(source)
    if not src.exists():
        raise Fail("FS_MISSING", f"{source} does not exist.")
    if src.is_file() and src.suffix.lower() != ".zip":
        return src.read_text(encoding="utf-8-sig"), {}, {}
    base, tmp = _extract(source, root)
    try:
        files = sorted(f for f in base.rglob("*.md") if f.is_file() and not is_noise(f.name))
        if not files:
            raise Fail("INTAKE_NO_TOPICS", f"{source} has no .md files in it.")
        sibling: dict[str, dict[str, bytes]] = {}
        for f in files:
            tid = parse(f, "pasted", text=f.read_text(encoding="utf-8-sig")).front.get("topic_id", "")
            adir = f.parent / "assets"
            if tid and adir.is_dir():
                sibling[tid] = {p.name: p.read_bytes() for p in sorted(adir.iterdir()) if p.is_file() and not is_noise(p.name)}
        text = "\n\n".join(f.read_text(encoding="utf-8-sig") for f in files)
        return text, sibling, _flat_assets(base)
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def split_topics(text: str) -> list[tuple[int, str]]:
    """Split pasted text into (first line number, topic text) blocks on topic front matter."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    starts = []
    for i, line in enumerate(lines):
        if line.strip() != "---":
            continue
        # An opening front matter delimiter: closes within a few lines and names a topic_id.
        close = next((j for j in range(i + 1, min(i + 10, len(lines))) if lines[j].strip() == "---"), None)
        if close and any(re.match(r"^\s*topic_id\s*:", w) for w in lines[i + 1:close]):
            starts.append(i)
    blocks = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else len(lines)
        chunk = lines[s:e]
        # Claude often wraps each topic in a code fence; drop fences at the edges only.
        while chunk and (not chunk[-1].strip() or FENCE.match(chunk[-1])):
            chunk.pop()
        blocks.append((s + 1, "\n".join(chunk).rstrip() + "\n"))
    return blocks


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    text, sibling_assets, flat_assets = _read_source(args.source, target.root)
    if args.assets_source:
        extra_base, extra_tmp = _extract(args.assets_source, target.root)
        try:
            for name, items in _flat_assets(extra_base).items():
                flat_assets.setdefault(name, []).extend(items)
        finally:
            if extra_tmp is not None:
                shutil.rmtree(extra_tmp, ignore_errors=True)
    blocks = split_topics(text)
    if not blocks:
        raise Fail("INTAKE_NO_TOPICS", "No topic front matter (--- then topic_id: ...) was found in the pasted text.",
                   hint="Paste the markdown exactly as Claude produced it, front matter included.")
    parts = target.rel.split("/") if target.rel != "." else []
    for first_line, block in blocks:
        p = parse(Path("pasted.md"), "pasted", text=block)
        tid = p.front.get("topic_id", "")
        r = TopicResult(tid or f"line {first_line}", target.rel)
        env.results.append(r)
        if not TOPIC_ID_RE.match(tid):
            r.diagnostics.append(Diagnostic("MD_TOPIC_ID_FORMAT", f"The topic starting at pasted line {first_line} has topic_id '{tid}'.",
                                            file="pasted", line=first_line))
            r.ok = False
            continue
        m, u, c = tid.split("-")
        t = Topic(target.root, m, u, c)
        r.rel = t.rel
        r.extra["path"] = f"{t.rel}/topic.md"
        if parts and (parts[0] != m or (len(parts) > 1 and parts[1] != u) or (len(parts) > 2 and parts[2] != c)):
            r.diagnostics.append(Diagnostic("INTAKE_NOT_IN_MAP", f"{tid} is outside {target.rel}, where this paste was aimed.", topic=tid))
            r.ok = False
            continue
        if not (target.root / m / "course-map.md").is_file() or module_context(target.root, m).course_map.find(u, c) is None:
            r.diagnostics.append(Diagnostic("INTAKE_NOT_IN_MAP", f"{tid} is not in {m}/course-map.md.", topic=tid,
                                            hint="Add it to the course map first, or check the topic_id."))
            r.extra["action"] = "refused"
            r.ok = False
            continue
        dest = t.src("en")
        if dest.exists():
            current = dest.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
            if current.rstrip() == block.rstrip():
                r.extra["action"] = "unchanged"
                r.diagnostics.append(Diagnostic("INTAKE_UNCHANGED", f"{t.rel}/topic.md already holds this text.", topic=tid))
            else:
                diff = "".join(difflib.unified_diff(current.splitlines(True), block.splitlines(True),
                                                    fromfile=f"{t.rel}/topic.md (on disk)", tofile="pasted"))
                r.extra["diff"] = diff
                if not args.replace:
                    r.extra["action"] = "exists_differs"
                    r.diagnostics.append(Diagnostic("INTAKE_EXISTS_DIFFERS", f"{t.rel}/topic.md exists and differs from the paste; nothing was written.",
                                                    topic=tid, file="topic.md", data={"diff": diff},
                                                    hint="Review the diff, or pass --replace (the UI: tick Replace) to overwrite it."))
                    r.ok = False
                    continue
                if args.dry_run:
                    r.extra["action"] = "would_replace"
                else:
                    fsutil.write_text(dest, block)
                    r.extra["action"] = "replaced"
                    r.diagnostics.append(Diagnostic("INTAKE_REPLACED", f"{t.rel}/topic.md existed and differed; replaced because --replace was set.",
                                                    topic=tid, file="topic.md", data={"diff": diff}))
        elif args.dry_run:
            r.extra["action"] = "would_write"
        else:
            fsutil.write_text(dest, block)
            r.extra["action"] = "written"
            r.diagnostics.append(Diagnostic("INTAKE_WRITTEN", f"Wrote {t.rel}/topic.md.", topic=tid, file="topic.md"))
        # The topic's own images: matched by filename against what its own slides
        # reference, not copied wholesale, so a shared assets pool with other topics'
        # images in it doesn't pull in anything this topic doesn't actually use. Placed
        # before validate runs below, so a reference to one of them doesn't report as
        # missing just because this intake also happened to bring the image in.
        if not args.dry_run and r.extra.get("action") in ("written", "unchanged", "replaced"):
            referenced = sorted({Path(im.path).name for s in p.slides for im in s.images})
            for name in referenced:
                data = sibling_assets.get(tid, {}).get(name)
                if data is None:
                    candidates = flat_assets.get(name)
                    if not candidates:
                        continue
                    if len({bytes(d) for _, d in candidates}) > 1:
                        where = ", ".join(str(cp) for cp, _ in candidates)
                        r.diagnostics.append(Diagnostic("INTAKE_ASSET_AMBIGUOUS",
                                                        f"'{name}' matched more than one differing file in the source ({where}); used the first.",
                                                        topic=tid, file=f"assets/{name}"))
                    data = candidates[0][1]
                fsutil.write_bytes(t.assets / name, data)
                r.diagnostics.append(Diagnostic("INTAKE_ASSET_WRITTEN", f"Wrote {t.rel}/assets/{name}.", topic=tid, file=f"assets/{name}"))
        # Validate in place, exactly as bcn validate would, and record it as that step's result.
        if dest.exists():
            cfg = load(t.root, t.module_dir)
            _, diags = validate_topic(cfg, t, "en")
            vr = TopicResult(tid, t.rel, diagnostics=diags)
            vr.ok = not any(d.level == "error" for d in diags)
            if not args.dry_run:
                venv = Envelope("validate", t.rel, t.root)
                fsutil.write_json(t.step_file("validate", "en"), venv.topic_envelope(vr))
            r.extra["validate_ok"] = vr.ok
            r.diagnostics.extend(diags)
        r.ok = not any(d.level == "error" for d in r.diagnostics)
    env.extra["topics_found"] = len(blocks)
