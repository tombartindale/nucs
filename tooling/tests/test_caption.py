"""bcn caption: single-cue edits, and bulk edits via --cues-json."""

from .test_cli import bcn

SRT = (
    "1\n00:00:00,000 --> 00:00:02,000\nFirst cue\n\n"
    "2\n00:00:02,000 --> 00:00:04,000\nSecond cue\n\n"
    "3\n00:00:04,000 --> 00:00:06,000\nThird cue\n"
)


def _topic(tree):
    t = tree / "KV7015/U01/T01"
    (t / "edit").mkdir()
    (t / "edit/master.srt").write_text(SRT)
    return t


def test_single_cue_edit(tree, capsys):
    t = _topic(tree)
    code, env = bcn(capsys, "caption", str(t), "--lang", "en", "--cue", "2", "--caption-text", "Second cue, fixed.")
    assert code == 0
    r = env["results"][0]
    assert r["written"] is True and r["cues"] == [2]
    text = (t / "edit/master.srt").read_text()
    assert "Second cue, fixed." in text and "First cue" in text and "Third cue" in text


def test_single_cue_noop_when_text_unchanged(tree, capsys):
    t = _topic(tree)
    before = (t / "edit/master.srt").read_text()
    code, env = bcn(capsys, "caption", str(t), "--lang", "en", "--cue", "1", "--caption-text", "First cue")
    assert code == 0 and env["results"][0]["written"] is False
    assert (t / "edit/master.srt").read_text() == before


def test_bulk_edit_writes_every_cue_in_one_pass(tree, capsys):
    t = _topic(tree)
    history_dir = t / "edit/.history"
    before_history = list(history_dir.glob("*")) if history_dir.exists() else []
    code, env = bcn(capsys, "caption", str(t), "--lang", "en", "--cues-json",
                     '{"1": "First, fixed.", "3": "Third, fixed."}')
    assert code == 0
    r = env["results"][0]
    assert r["written"] is True and r["cues"] == [1, 3]
    text = (t / "edit/master.srt").read_text()
    assert "First, fixed." in text and "Third, fixed." in text and "Second cue" in text
    # One snapshot for the whole batch, not one per cue.
    after_history = list(history_dir.glob("*"))
    assert len(after_history) == len(before_history) + 1


def test_bulk_edit_skips_cues_whose_text_is_unchanged(tree, capsys):
    t = _topic(tree)
    code, env = bcn(capsys, "caption", str(t), "--lang", "en", "--cues-json",
                     '{"1": "First cue", "2": "Second cue, changed."}')
    assert code == 0
    assert env["results"][0]["cues"] == [2]   # cue 1's text matched what was already there


def test_bulk_edit_all_unchanged_is_a_full_noop(tree, capsys):
    t = _topic(tree)
    code, env = bcn(capsys, "caption", str(t), "--lang", "en", "--cues-json", '{"1": "First cue"}')
    assert code == 0
    assert env["results"][0]["written"] is False and env["results"][0]["cues"] == []


def test_bulk_edit_rejects_out_of_range_cue(tree, capsys):
    t = _topic(tree)
    code, env = bcn(capsys, "caption", str(t), "--lang", "en", "--cues-json", '{"1": "ok", "9": "too far"}')
    assert code == 1   # r.ok=False set before the raise, same convention as the single-cue case
    assert "9" in env["diagnostics"][0]["message"]


def test_bulk_edit_rejects_empty_text(tree, capsys):
    t = _topic(tree)
    code, env = bcn(capsys, "caption", str(t), "--lang", "en", "--cues-json", '{"1": "   "}')
    assert code == 2
    assert "USAGE" in env["diagnostics"][0]["code"]


def test_bulk_edit_rejects_invalid_json(tree, capsys):
    t = _topic(tree)
    code, env = bcn(capsys, "caption", str(t), "--lang", "en", "--cues-json", "not json")
    assert code == 2
    assert "USAGE" in env["diagnostics"][0]["code"]


def test_cues_json_cannot_combine_with_cue_flag(tree, capsys):
    t = _topic(tree)
    code, env = bcn(capsys, "caption", str(t), "--lang", "en", "--cue", "1", "--caption-text", "x",
                     "--cues-json", '{"2": "y"}')
    assert code == 2
    assert "USAGE" in env["diagnostics"][0]["code"]


def test_caption_needs_cue_or_cues_json(tree, capsys):
    t = _topic(tree)
    code, env = bcn(capsys, "caption", str(t), "--lang", "en")
    assert code == 2
    assert "USAGE" in env["diagnostics"][0]["code"]
