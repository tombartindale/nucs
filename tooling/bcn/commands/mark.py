"""bcn mark: tick off progress that happens outside the system.

  bcn mark <path> --mark filmed            the recording session has happened
  bcn mark <path> --mark edited            the edit is done, not yet uploaded
  bcn mark <path> --mark edited --clear    take a mark off again

Tracks where production is up to before the editor's files arrive. Marks are
progress markers only: the recorded stage is still decided by edit/master.mp4
and edit/master.srt, never by these. Written to each topic's review.json with
who and when. Works on a unit as well as a topic, since a unit is filmed in one
session.
"""

from __future__ import annotations

import argparse
import getpass

from .. import reviewfile
from ..envelope import Envelope, Fail, TopicResult
from ..tree import Target

HELP = "tick off filming or editing done outside the system"


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--mark", required=True, choices=reviewfile.MARKS, help="which mark to set")
    p.add_argument("--clear", action="store_true", help="take the mark off")
    p.add_argument("--by", default=None, help="who is marking it (default: login name)")


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    topics = [t for t in target.topics if t.dir.is_dir()]
    if not topics:
        raise Fail("USAGE", "There are no topic folders under that path to mark.")
    for t in topics:
        if args.clear:
            reviewfile.clear_mark(t, args.mark)
        else:
            reviewfile.set_mark(t, args.mark, args.by or getpass.getuser())
        env.results.append(TopicResult(t.id, t.rel, extra={"marks": reviewfile.load(t)["marks"]}))
