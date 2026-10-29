"""bcn session: mark that a topic's recording session has happened.

  bcn session <path>            mark every topic under path as recorded in session
  bcn session <path> --clear    take the mark off again

Tracks where filming is up to before the editor sends anything back. It is a
progress marker only: the recorded stage is still decided by edit/master.mp4 and
edit/master.srt, never by this. Written to each topic's review.json with who and
when. Works on a unit as well as a topic, since a unit is recorded in one session.
"""

from __future__ import annotations

import argparse
import getpass

from .. import reviewfile
from ..envelope import Envelope, Fail, TopicResult
from ..tree import Target

HELP = "mark that a topic's recording session has happened"


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--clear", action="store_true", help="take the mark off")
    p.add_argument("--by", default=None, help="who is marking it (default: login name)")


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    topics = [t for t in target.topics if t.dir.is_dir()]
    if not topics:
        raise Fail("USAGE", "There are no topic folders under that path to mark.")
    for t in topics:
        if args.clear:
            reviewfile.clear_session(t)
        else:
            reviewfile.set_session(t, args.by or getpass.getuser())
        env.results.append(TopicResult(t.id, t.rel, extra={"session": reviewfile.load(t)["session"]}))
