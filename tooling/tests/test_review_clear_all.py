"""bcn review --clear-all: reset every recorded decision for a topic in one shot."""

import json

from .test_cli import bcn


def _topic(tree):
    t = tree / "KV7015/U01/T01"
    t.mkdir(exist_ok=True)
    review = {
        "schema": 1,
        "cue_overrides": {"3": {"timecode": "00:01:12.000", "by": "test", "at": "2026-01-01T00:00:00Z"}},
        "transcripts": {
            "mi-aaaa111111": {"decision": "correct", "text": "fixed", "script": "x", "srt": "y", "cue": 5, "by": "ui", "at": "2026-01-01T00:00:00Z"},
            "mi-bbbb222222": {"decision": "accept", "text": None, "script": "p", "srt": "q", "cue": 9, "by": "ui", "at": "2026-01-01T00:00:00Z"},
        },
        "acknowledged": {"SOME_FINGERPRINT": {"by": "test", "at": "2026-01-01T00:00:00Z"}},
    }
    (t / "review.json").write_text(json.dumps(review, indent=2) + "\n")
    return t


def test_clear_all_empties_transcripts_only(tree, capsys):
    t = _topic(tree)
    code, env = bcn(capsys, "review", str(t), "--clear-all")
    assert code == 0
    assert env["results"][0]["cleared"] == 2
    data = json.loads((t / "review.json").read_text())
    assert data["transcripts"] == {}
    # Hand-set slide boundaries and acknowledged findings are a different concern - untouched.
    assert data["cue_overrides"] == {"3": {"timecode": "00:01:12.000", "by": "test", "at": "2026-01-01T00:00:00Z"}}
    assert data["acknowledged"] == {"SOME_FINGERPRINT": {"by": "test", "at": "2026-01-01T00:00:00Z"}}


def test_clear_all_on_already_empty_topic_is_a_noop(tree, capsys):
    t = tree / "KV7015/U01/T01"
    code, env = bcn(capsys, "review", str(t), "--clear-all")
    assert code == 0
    assert env["results"][0]["cleared"] == 0


def test_clear_all_rejects_item(tree, capsys):
    t = _topic(tree)
    code, env = bcn(capsys, "review", str(t), "--clear-all", "--item", "mi-aaaa111111")
    assert code == 2
    assert "USAGE" in env["diagnostics"][0]["code"]
    data = json.loads((t / "review.json").read_text())
    assert data["transcripts"]   # nothing was cleared - the bad call never ran the per-topic loop


def test_clear_all_rejects_a_whole_module_target(tree, capsys):
    _topic(tree)
    code, env = bcn(capsys, "review", str(tree / "KV7015"), "--clear-all")
    assert code == 2
    assert "USAGE" in env["diagnostics"][0]["code"]


def test_clear_all_cannot_combine_with_correct(tree, capsys):
    t = _topic(tree)
    code, env = bcn(capsys, "review", str(t), "--clear-all", "--correct", "x")
    assert code == 2   # argparse's mutually exclusive group rejects this before run() is reached
