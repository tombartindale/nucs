"""bcn docedit: replace a module document's text, safely, then validate the whole module.

  bcn docedit <module> --doc course-map.md --from FILE [--expect-sha SHA] [--dry-run]
  bcn docedit <module> --doc U01/activity.md --from FILE
  bcn docedit <module> --doc assignment-1.md --from FILE

--doc must be one of the module documents bcn already recognises: course-map.md,
assets.md, reading-list.md, assignment-N.md, or a unit's activity.md. Anything
else is refused — this is the one place that decides what counts as an editable
module document, so nothing else needs its own copy of that list.

Generalizes bcn edit's sha-conflict and .history/ versioning (edit.py works on
exactly one topic's topic.md/topic.zh.md; module documents have no topic, no
language split, and are validated by module_documents() instead of
validate_topic()). --dry-run runs the *whole* module's checks with the edited
text substituted in for this one file, so a cross-file effect (e.g. editing
course-map.md's outcomes) shows up before you save, the same reason
validate_topic() takes a text override for topic.md.
"""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

from .. import fsutil
from ..envelope import Diagnostic, Envelope, Fail, TopicResult
from ..tree import Target
from .validate import module_documents

HELP = "save edited text for a module document, then validate the module"
KEEP_VERSIONS = 30
DOC_NAME_RE = re.compile(r"^(?:course-map\.md|assets\.md|reading-list\.md|assignment-\d+\.md|U\d{2}/activity\.md)$")


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--doc", required=True, metavar="RELATIVE_PATH",
                   help="which module document, relative to the module (e.g. course-map.md, U01/activity.md)")
    p.add_argument("--from", dest="source", required=True, help="file holding the edited text")
    p.add_argument("--expect-sha", help="SHA-256 of the file as it was when editing began")
    p.add_argument("--dry-run", action="store_true", help="validate the text; write nothing")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    if target.level != "module":
        raise Fail("USAGE", "docedit works on one module directory.")
    if not DOC_NAME_RE.match(args.doc):
        raise Fail("USAGE", f"'{args.doc}' is not a module document bcn recognises.",
                   hint="Use course-map.md, assets.md, reading-list.md, assignment-N.md, or U01/activity.md.")
    m = target.modules[0]
    mdir = target.root / m
    doc_path = mdir / args.doc
    rel = f"{m}/{args.doc}"
    source = Path(args.source)
    if not source.is_file():
        raise Fail("FS_MISSING", f"{args.source} does not exist.")
    text = source.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    new_bytes = text.encode("utf-8")
    old_bytes = doc_path.read_bytes() if doc_path.is_file() else None
    current_sha = _sha(old_bytes) if old_bytes is not None else None

    r = TopicResult(rel, rel)
    env.results.append(r)
    if args.expect_sha and current_sha and args.expect_sha != current_sha and not args.dry_run:
        r.ok = False
        r.extra["current_sha256"] = current_sha
        raise Fail("EDIT_CONFLICT", f"{args.doc} has changed on disk since you started editing; nothing was written.",
                   file=rel, hint="Reload to see the new version (your text is kept to copy from), or save again to overwrite it.")

    diags = module_documents(target, override_path=doc_path, override_text=text)
    r.diagnostics.extend(diags)

    if args.dry_run:
        r.extra.update({"written": False, "sha256": current_sha})
    elif old_bytes == new_bytes:
        r.extra.update({"written": False, "sha256": current_sha})
    else:
        if old_bytes is not None:
            fsutil.save_with_history(doc_path, old_bytes, keep=KEEP_VERSIONS)
        fsutil.write_bytes(doc_path, new_bytes)
        r.extra.update({"written": True, "sha256": _sha(new_bytes)})
        r.diagnostics.append(Diagnostic("EDIT_SAVED", f"Saved {args.doc}.", file=rel))
    # Saving succeeded even if the document still has problems: they are reported, not a
    # failure to save (matches bcn edit's own rule for topic.md).
    r.ok = True
    env.extra["validation_ok"] = not any(d.level == "error" for d in diags)
    env.extra["counts"] = {lvl: sum(1 for d in diags if d.level == lvl) for lvl in ("error", "warn", "info")}
    env.extra["diagnostics_are_validation"] = True
