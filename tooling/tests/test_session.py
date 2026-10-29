"""bcn session: the hand-ticked "recording session has happened" marker."""

import json

from .test_cli import bcn


def _status(capsys, tree):
    code, env = bcn(capsys, "status", str(tree / "KV7015"))
    assert code == 0
    return {r["topic"]: r for r in env["results"]}


def test_session_marks_and_clears_one_topic(tree, capsys):
    t = tree / "KV7015/U01/T01"
    t.mkdir(exist_ok=True)
    code, env = bcn(capsys, "session", str(t), "--by", "tom")
    assert code == 0
    assert env["results"][0]["session"]["by"] == "tom"
    assert json.loads((t / "review.json").read_text())["session"]["by"] == "tom"
    st = _status(capsys, tree)["KV7015-U01-T01"]
    assert st["session"]["by"] == "tom"
    # A progress marker, not a stage: nothing has arrived from the editor yet.
    assert st["en"]["stage_index"] < st["en"]["stages"].index("recorded")

    code, env = bcn(capsys, "session", str(t), "--clear")
    assert code == 0
    assert _status(capsys, tree)["KV7015-U01-T01"]["session"] is None


def test_session_on_a_unit_marks_every_topic_in_it(tree, capsys):
    for code in ("T01", "T02"):
        (tree / "KV7015/U01" / code).mkdir(exist_ok=True)
    code, env = bcn(capsys, "session", str(tree / "KV7015/U01"))
    assert code == 0
    st = _status(capsys, tree)
    assert st["KV7015-U01-T01"]["session"] and st["KV7015-U01-T02"]["session"]
