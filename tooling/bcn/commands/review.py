"""bcn review: list script/SRT divergences (mishearings, paraphrases, cuts, insertions), and record accept or correct.

Decisions are written to the topic's review.json, which is content and
survives build/ being deleted. A correction is applied to the delivered subtitle
text by bcn subtitles; timings are never touched.
"""

from __future__ import annotations

import argparse
import getpass

from .. import fsutil, reviewfile
from ..envelope import Envelope, Fail, TopicResult
from ..tree import Target

HELP = "list script/SRT divergences, or accept/correct one"


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--item", help="divergence id from cues-report.json")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--accept", action="store_true", help="the SRT reading is fine as it is")
    g.add_argument("--correct", metavar="TEXT", help="the SRT should read TEXT here")
    g.add_argument("--clear", action="store_true", help="remove a previous decision")
    p.add_argument("--applied", action="store_true",
                   help="with --correct: TEXT is already live in the SRT (e.g. written directly by the Caption "
                        "Editor) - record it, but never let bcn subtitles try to substitute it in again")
    p.add_argument("--by", default=None, help="who made the decision (default: login name)")


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    deciding = args.accept or args.correct is not None or args.clear
    if deciding and (target.level != "topic" or not args.item):
        raise Fail("USAGE", "Deciding needs a single topic directory and --item.")
    if args.item and not deciding:
        raise Fail("USAGE", "--item needs one of --accept, --correct TEXT or --clear.")
    if args.correct is not None and not args.correct.strip():
        raise Fail("USAGE", "--correct needs the text the subtitle should read.")
    for t in target.topics:
        r = TopicResult(t.id, t.rel)
        report = fsutil.read_json(t.cues_report) or {}
        # Every kind of divergence can be reviewed, not just mishearings: a paraphrase, a cut or an
        # insertion is as much "what should this cue read" as a mistranscription is. A cut that spans
        # a slide break is the exception - that is a boundary to place (bcn cues --set), not a cue to edit.
        items = [d for d in report.get("divergences", [])
                 if d.get("kind") in ("mistranscription", "paraphrase", "cut", "insertion")
                 and not (d.get("kind") == "cut" and d.get("spans_boundary"))]
        if deciding and args.clear:
            # Clearing only needs the id to already be a key in review.json - not a live match in
            # cues-report.json. A decision's id changes once the cue moves on (a new correction, or
            # cues.py's own hash scheme changing), so by the time someone wants to undo an old
            # decision it is very often already "orphaned" (shown via cues.py's synthesised diagnostic,
            # not among the live divergences) - requiring a live match here would make Undo silently
            # impossible for exactly the decisions most likely to need undoing.
            data = reviewfile.load(t)
            if args.item not in data["transcripts"]:
                raise Fail("REVIEW_UNKNOWN_ITEM", f"No decision recorded for '{args.item}' in {t.rel}/review.json.",
                           topic=t.id, hint="There is nothing to undo.")
            data["transcripts"].pop(args.item, None)
            reviewfile.save(t, data)
        elif deciding:
            item = next((d for d in items if d["id"] == args.item), None)
            if item is None:
                raise Fail("REVIEW_UNKNOWN_ITEM", f"No reviewable divergence '{args.item}' in {t.rel}/build/cues-report.json.",
                           topic=t.id, hint="Run bcn cues first, or list the items with bcn review.")
            reviewfile.decide(t, item, "accept" if args.accept else "correct", args.correct, args.by or getpass.getuser(),
                              applied=args.applied)
        decisions = reviewfile.load(t)["transcripts"]
        r.extra["items"] = [{**d, "review": decisions.get(d["id"])} for d in items]
        r.extra["unreviewed"] = sum(1 for d in items if d["id"] not in decisions)
        if report and not fsutil.is_fresh([t.cues_report], [t.src("en"), t.srt("en")]):
            r.extra["stale"] = True
        env.results.append(r)
