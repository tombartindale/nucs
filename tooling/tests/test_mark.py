"""bcn mark: hand-ticked filmed / edited progress markers."""

import json

from .test_cli import bcn


def _status(capsys, tree):
    code, env = bcn(capsys, "status", str(tree / "KV7015"))
    assert code == 0
    return {r["topic"]: r for r in env["results"]}


def test_mark_sets_and_clears_each_mark_independently(tree, capsys):
    t = tree / "KV7015/U01/T01"
    code, env = bcn(capsys, "mark", str(t), "--mark", "filmed", "--by", "tom")
    assert code == 0
    assert env["results"][0]["marks"]["filmed"]["by"] == "tom"
    bcn(capsys, "mark", str(t), "--mark", "edited", "--by", "tom")
    assert set(json.loads((t / "review.json").read_text())["marks"]) == {"filmed", "edited"}
    st = _status(capsys, tree)["KV7015-U01-T01"]
    assert set(st["marks"]) == {"filmed", "edited"}
    # Markers, not stages: nothing has arrived from the editor yet.
    assert st["en"]["stage_index"] < st["en"]["stages"].index("recorded")

    bcn(capsys, "mark", str(t), "--mark", "edited", "--clear")
    assert set(_status(capsys, tree)["KV7015-U01-T01"]["marks"]) == {"filmed"}


def test_mark_on_a_unit_marks_every_topic_in_it(tree, capsys):
    (tree / "KV7015/U01/T02").mkdir()
    code, env = bcn(capsys, "mark", str(tree / "KV7015/U01"), "--mark", "filmed")
    assert code == 0
    st = _status(capsys, tree)
    assert "filmed" in st["KV7015-U01-T01"]["marks"] and "filmed" in st["KV7015-U01-T02"]["marks"]


def test_mark_refuses_an_unknown_mark(tree, capsys):
    code, env = bcn(capsys, "mark", str(tree / "KV7015/U01/T01"), "--mark", "posted")
    assert code != 0
