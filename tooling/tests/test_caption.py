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


# -- bcn subtitles --lang zh: translators split and re-time cues ----------------------------------

ZH_SPLIT = (
    "1\n00:00:00,000 --> 00:00:01,100\n第一句\n\n"
    "2\n00:00:01,100 --> 00:00:02,300\n第一句的后半\n\n"
    "3\n00:00:02,300 --> 00:00:04,000\n第二句\n\n"
    "4\n00:00:04,000 --> 00:00:06,000\nThird cue\n"
)


def _zh_topic(tree):
    """English SRT with 3 cues; the Mandarin splits the first into two and moves every boundary."""
    from .conftest import age
    from .test_rules import zh_md
    t = _topic(tree)
    (t / "topic.zh.md").write_text(zh_md())
    (t / "edit/master.zh.srt").write_text(ZH_SPLIT)
    for f in ("topic.md", "topic.zh.md", "edit/master.srt", "edit/master.zh.srt"):
        age(t / f, 60)  # settled files, not ones still arriving by sync
    return t


def test_zh_subtitles_accept_split_and_retimed_cues(tree, capsys):
    t = _zh_topic(tree)
    code, env = bcn(capsys, "validate", str(t), "--lang", "zh")
    assert code == 0, env["diagnostics"]
    code, env = bcn(capsys, "subtitles", str(t), "--lang", "zh")
    codes = [d["code"] for d in env["diagnostics"]]
    assert code == 0 and env["ok"] is True, codes
    assert not any(c.startswith("SRT_ZH_CUE") or c == "SRT_ZH_TIMING" for c in codes)
    # The English line left in cue 4 is still caught, though it sits at a different position.
    untranslated = [d for d in env["diagnostics"] if d["code"] == "SRT_ZH_UNTRANSLATED"]
    assert [d["message"] for d in untranslated] == ["Cue 4 has the English text unchanged."]
    assert (t / "build/subtitles").is_dir()
