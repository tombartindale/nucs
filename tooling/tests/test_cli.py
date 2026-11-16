"""End-to-end behaviour of the CLI contract: envelope shape, exit codes, state on disk."""

import json

from bcn.cli import main

from .conftest import age, make_video, topic_md


def bcn(capsys, *args):
    code = main([*args, "--quiet"])
    out = capsys.readouterr().out
    return code, json.loads(out)


def test_envelope_shape_and_step_file(tree, capsys):
    code, env = bcn(capsys, "validate", str(tree / "KV7015/U01/T01"))
    assert code == 0 and env["ok"] is True
    for k in ("tool", "schema", "target", "ok", "started", "duration_ms", "results", "artifacts", "diagnostics"):
        assert k in env
    assert env["target"] == "KV7015/U01/T01"
    step = json.loads((tree / "KV7015/U01/T01/build/validate.json").read_text())
    assert step["ok"] is True and step["results"][0]["topic"] == "KV7015-U01-T01"


def test_directory_run_continues_past_failures(tree, capsys):
    t2 = tree / "KV7015/U01/T02"
    t2.mkdir()
    (t2 / "topic.md").write_text(topic_md("KV7015-U01-T02", slides=2))
    code, env = bcn(capsys, "validate", str(tree / "KV7015"))
    assert code == 1 and env["ok"] is False
    assert [r["ok"] for r in env["results"]] == [True, False]


def test_usage_error_is_an_envelope(capsys):
    code = main(["validate"])
    env = json.loads(capsys.readouterr().out)
    assert code == 2 and env["diagnostics"][0]["code"] == "USAGE"


def test_missing_input_exit_code(tree, capsys):
    code, env = bcn(capsys, "cues", str(tree / "KV7015/U01/T01"))
    assert code == 3 and env["diagnostics"][0]["code"] in ("FS_MISSING", "STEP_PREREQUISITE")


def test_cues_refuses_mandarin(tree, capsys):
    code, env = bcn(capsys, "cues", str(tree / "KV7015/U01/T01"), "--lang", "zh")
    assert code == 2


def test_status_stages_and_staleness(tree, capsys):
    t = tree / "KV7015/U01/T01"
    _, env = bcn(capsys, "status", str(tree))
    rows = {r["topic"]: r for r in env["results"]}
    assert rows["KV7015-U01-T01"]["en"]["stage"] == "drafted"
    assert rows["KV7015-U01-T01"]["en"]["next"] == "validate"
    assert rows["KV7015-U01-T02"]["en"]["stage"] == "planned"  # in the course map, no directory
    age(t / "topic.md", 60)
    bcn(capsys, "validate", str(t))
    _, env = bcn(capsys, "status", str(t))
    en = env["results"][0]["en"]
    assert en["stage"] == "validated" and not en["stale"] and en["next"] == "render"
    # Editing the source after validation leaves the stage but marks it stale.
    (t / "topic.md").write_text(topic_md() + "\n")
    _, env = bcn(capsys, "status", str(t))
    en = env["results"][0]["en"]
    assert en["stage"] == "validated" and en["stale"] and en["next"] == "validate"
    assert not list((t / "build").glob("status*.json")), "status must never write"


def test_status_blocks_on_sync_conflict(tree, capsys):
    (tree / "KV7015/U01/T01/topic-Laptop.md").write_text("x")
    _, env = bcn(capsys, "status", str(tree / "KV7015/U01/T01"))
    en = env["results"][0]["en"]
    assert en["blocked"] and en["blockers"][0]["code"] == "FS_SYNC_CONFLICT"


def test_cues_end_to_end(tree, capsys):
    t = tree / "KV7015/U01/T01"
    # An SRT that tracks the script exactly, one cue per sentence.
    from bcn.markdown import parse
    p = parse(t / "topic.md")
    blocks, time_ = [], 0.5
    for s in p.slides:
        for sentence in [x.strip() + "." for x in s.say_text.split(".") if x.strip()]:
            n = len(blocks) + 1
            dur = len(sentence.split()) / 2.5
            blocks.append(f"{n}\n00:{int(time_ // 60):02d}:{time_ % 60:06.3f} --> 00:{int((time_ + dur) // 60):02d}:{(time_ + dur) % 60:06.3f}\n{sentence}\n".replace(".", ",", 0))
            time_ += dur + 0.1
    srt_text = "\n".join(b.replace(" --> ", " --> ") for b in blocks)
    srt_text = srt_text.replace(".", ",").replace(",\n", ".\n")  # timecodes use commas, text keeps its full stops
    (t / "edit").mkdir()
    make_video(t / "edit/master.mp4", time_ + 2)
    (t / "edit/master.srt").write_text(srt_text)
    for f in ("topic.md", "edit/master.mp4", "edit/master.srt"):
        age(t / f, 30)
    bcn(capsys, "validate", str(t))
    code, env = bcn(capsys, "cues", str(t))
    assert code == 0, env["diagnostics"]
    csv = (t / "build/KV7015-U01-T01.cues.csv").read_text().splitlines()
    assert csv[0] == "slide,timecode" and csv[1] == "1,00:00:00.000" and len(csv) == 7
    assert env["results"][0]["min_confidence"] == 1.0
    # Idempotent: a second run skips and leaves the sheet as it was.
    before = (t / "build/KV7015-U01-T01.cues.csv").stat().st_mtime
    code, env = bcn(capsys, "cues", str(t))
    assert code == 0 and env["results"][0]["skipped"] is True
    assert (t / "build/KV7015-U01-T01.cues.csv").stat().st_mtime == before

    # A mishearing is reported, reviewed, and corrected in the delivered subtitles.
    assert "sentence number 1 for slide 3" in srt_text
    (t / "edit/master.srt").write_text(srt_text.replace("sentence number 1 for slide 3", "sentence lumber 1 for slide 3", 1))
    for f in ("edit/master.mp4", "edit/master.srt"):
        age(t / f, 10)
    for f in ("build/cues.json", "build/cues-report.json", "build/KV7015-U01-T01.cues.csv"):
        age(t / f, 20)  # the previous result predates the new SRT
    code, env = bcn(capsys, "cues", str(t))
    items = [d for d in env["diagnostics"] if d["code"] == "CUE_MISTRANSCRIPTION"]
    assert items and items[0]["data"]["script"] == "number" and items[0]["data"]["srt"] == "lumber"
    code, env = bcn(capsys, "review", str(t), "--item", items[0]["data"]["id"], "--correct", "number", "--by", "test")
    assert env["results"][0]["unreviewed"] == 0
    code, env = bcn(capsys, "subtitles", str(t))
    assert code == 0 and env["results"][0]["modified"] is True
    out = (t / "build/subtitles/KV7015-U01-T01.en.srt").read_text()
    assert "lumber" not in out and "number 1 for slide 3" in out

    # A reworded passage and a dropped sentence are reviewable too, not just mishearings: flagged
    # as warnings until a decision exists, then info with the decision attached.
    srt_text = (t / "edit/master.srt").read_text().replace("sentence number 1 for slide 3", "sentence number 1 for slide 3")
    assert "sentence number 1 for slide 4" in srt_text
    (t / "edit/master.srt").write_text(srt_text.replace("sentence number 1 for slide 4", "a completely different remark about things", 1))
    for f in ("edit/master.mp4", "edit/master.srt"):
        age(t / f, 10)
    for f in ("build/cues.json", "build/cues-report.json", "build/KV7015-U01-T01.cues.csv"):
        age(t / f, 20)
    code, env = bcn(capsys, "cues", str(t))
    other = [d for d in env["diagnostics"] if d["code"] in ("CUE_PARAPHRASE", "CUE_CUT", "CUE_INSERTION") and d["data"].get("review") is None]
    assert other and all(d["level"] == "warn" for d in other), env["diagnostics"]
    code, env = bcn(capsys, "review", str(t), "--item", other[0]["data"]["id"], "--accept", "--by", "test")
    assert code == 0, env["diagnostics"]
    code, env = bcn(capsys, "cues", str(t), "--force")
    done = [d for d in env["diagnostics"] if d["data"].get("id") == other[0]["data"]["id"]]
    assert done and done[0]["level"] == "info" and done[0]["data"]["review"]["decision"] == "accept"


def test_schema_and_codes(capsys):
    code, env = bcn(capsys, "schema", "status")
    assert code == 0 and env["json_schema"]["properties"]["tool"]["const"] == "status"
    code, env = bcn(capsys, "codes")
    assert any(c["code"] == "CUE_LOW_CONFIDENCE" for c in env["codes"])


def test_intake_refuses_to_overwrite(tree, capsys):
    paste = tree.parent / "paste.md"
    paste.write_text("```markdown\n" + topic_md().replace("Point one", "Point uno") + "```\n")
    code, env = bcn(capsys, "intake", str(tree / "KV7015"), "--from", str(paste))
    r = env["results"][0]
    assert code == 1 and r["action"] == "exists_differs" and "Point uno" in r["diff"]
    assert "Point uno" not in (tree / "KV7015/U01/T01/topic.md").read_text()
    paste.write_text(topic_md("KV7015-U01-T02"))
    code, env = bcn(capsys, "intake", str(tree / "KV7015"), "--from", str(paste))
    assert env["results"][0]["action"] == "written" and (tree / "KV7015/U01/T02/topic.md").is_file()
    paste.write_text(topic_md("KV7015-U09-T01"))
    code, env = bcn(capsys, "intake", str(tree / "KV7015"), "--from", str(paste))
    assert env["results"][0]["action"] == "refused"


def test_acknowledge_and_severity(tree, capsys):
    t = tree / "KV7015/U01/T01"
    (t / "topic.md").write_text(topic_md().replace("gives one example", "gives one example from 12 March", 1))
    code, env = bcn(capsys, "validate", str(t))
    dates = [d for d in env["diagnostics"] if d["code"] == "MD_DATE"]
    assert code == 1 and len(dates) == 1
    fp = dates[0]["data"]["fingerprint"]
    code, env = bcn(capsys, "ack", str(t), "--fingerprint", fp, "--note", "an example", "--by", "test")
    assert code == 0 and env["ok"] and env["acknowledged"][fp]["note"] == "an example"
    step = json.loads((t / "build/validate.json").read_text())
    assert step["ok"] and step["diagnostics"][0]["level"] == "info"
    # Structural findings cannot be acknowledged.
    code, env = bcn(capsys, "ack", str(t), "--fingerprint", "MD_SAY_MISSING|en|s1|")
    assert code == 2
    # Withdrawn, it blocks again; made a warning programme-wide, it does not.
    code, env = bcn(capsys, "ack", str(t), "--fingerprint", fp, "--clear")
    assert code == 1
    (tree / "programme.toml").write_text('[validate.severity]\nMD_DATE = "warn"\n')
    from bcn import config
    config._cache.clear()
    code, env = bcn(capsys, "validate", str(t))
    assert code == 0 and [d["level"] for d in env["diagnostics"] if d["code"] == "MD_DATE"] == ["warn"]


def test_superseded_prerequisite_failure_is_not_a_blocker(tree, capsys):
    # render refused while validate was failing; once validate passes, that refusal is history.
    t = tree / "KV7015/U01/T01"
    good = (t / "topic.md").read_text()
    (t / "topic.md").write_text(good.replace("title: A topic title", "title: A topic title\nextra: x"))
    bcn(capsys, "validate", str(t))
    code, env = bcn(capsys, "render", str(t))
    assert env["diagnostics"][0]["code"] == "STEP_PREREQUISITE"
    (t / "topic.md").write_text(good)
    age(t / "topic.md", 5)
    age(t / "build/render.json", 3)
    bcn(capsys, "validate", str(t))
    _, env = bcn(capsys, "status", str(t))
    en = env["results"][0]["en"]
    assert not en["blocked"] and en["next"] == "render" and en["blockers"] == []


def test_edit_dry_run_save_history_and_conflict(tree, capsys):
    import hashlib
    t = tree / "KV7015/U01/T01"
    original = (t / "topic.md").read_bytes()
    sha = hashlib.sha256(original).hexdigest()
    draft = tree.parent / "draft.md"

    # Dry run: problems reported, nothing written.
    draft.write_text(original.decode().replace("title: A topic title", "title: A topic title\nextra: x"))
    code, env = bcn(capsys, "edit", str(t), "--from", str(draft), "--dry-run")
    assert code == 0 and env["validation_ok"] is False and env["results"][0]["written"] is False
    assert (t / "topic.md").read_bytes() == original

    # Save: written, previous version kept, validate result recorded.
    draft.write_text(original.decode().replace("Point one", "Point uno", 1))
    code, env = bcn(capsys, "edit", str(t), "--from", str(draft), "--expect-sha", sha)
    assert code == 0 and env["results"][0]["written"] is True and env["validation_ok"] is True
    assert "Point uno" in (t / "topic.md").read_text()
    assert [p.read_bytes() for p in (t / ".history").iterdir()] == [original]
    assert json.loads((t / "build/validate.json").read_text())["ok"] is True

    # Someone else changed it since we loaded it: refused, nothing lost.
    code, env = bcn(capsys, "edit", str(t), "--from", str(draft), "--expect-sha", sha)
    assert code == 1 and env["diagnostics"][-1]["code"] == "EDIT_CONFLICT"
    assert "Point uno" in (t / "topic.md").read_text()


def test_translation_ready_without_video(tree, capsys):
    """The English SRT alone is enough to cue, prepare subtitles and send for translation;
    the video can arrive later, and its arrival puts cues out of date for the pair checks."""
    t = tree / "KV7015/U01/T01"
    from bcn.markdown import parse
    p = parse(t / "topic.md")
    blocks, time_ = [], 0.5
    for s in p.slides:
        for sentence in [x.strip() + "." for x in s.say_text.split(".") if x.strip()]:
            dur = len(sentence.split()) / 2.5
            a, b = time_, time_ + dur
            blocks.append(f"{len(blocks) + 1}\n00:{int(a // 60):02d}:{a % 60:06.3f} --> 00:{int(b // 60):02d}:{b % 60:06.3f}\n{sentence}\n")
            time_ = b + 0.1
    srt_text = "\n".join(
        "\n".join(ln.replace(".", ",") if "-->" in ln else ln for ln in blk.splitlines()) + "\n" for blk in blocks)
    (t / "edit").mkdir()
    (t / "edit/master.srt").write_text(srt_text)
    for f in ("topic.md", "edit/master.srt"):
        age(t / f, 30)
    bcn(capsys, "validate", str(t))
    _, env = bcn(capsys, "status", str(t))
    assert env["results"][0]["en"]["next"] == "cues"   # not render: cues does not need slides
    code, env = bcn(capsys, "cues", str(t))
    assert code == 0, env["diagnostics"]
    assert env["results"][0]["video_checked"] is False
    assert json.loads((t / "build/cues-report.json").read_text())["video"] is None
    code, env = bcn(capsys, "subtitles", str(t))
    assert code == 0, env["diagnostics"]
    _, env = bcn(capsys, "status", str(t))
    res = env["results"][0]
    assert res["zh"]["next"] == "translation_export"
    assert res["en"]["steps"]["cues"]["fresh"] is True
    assert res["en"]["next"] == "render"   # render was never run, and the subtitle side did not wait for it

    # A slide-timing error fails cues (compose needs it resolved) but does not hold up the
    # subtitle side: subtitles still runs and the topic can still be sent for translation.
    code, env = bcn(capsys, "cues", str(t), "--set", "99=00:00:01.000")
    assert code != 0 and {d["code"] for d in env["diagnostics"] if d["level"] == "error"} == {"CUE_SHEET_INVALID"}
    code, env = bcn(capsys, "subtitles", str(t), "--force")
    assert code == 0, env["diagnostics"]
    _, env = bcn(capsys, "status", str(t))
    res = env["results"][0]
    assert res["zh"]["next"] == "translation_export" and res["en"]["next"] == "cues"
    assert res["en"]["steps"]["cues"]["slides_to_place"] == 0   # amber in the UI, not red
    bcn(capsys, "cues", str(t), "--unset", "99")

    make_video(t / "edit/master.mp4", time_ + 2)
    age(t / "edit/master.mp4", 60)   # even an old video (copied with its mtime) makes cues re-run
    _, env = bcn(capsys, "status", str(t))
    assert env["results"][0]["en"]["steps"]["cues"]["fresh"] is False


def test_subtitles_ignores_only_slide_timing_errors():
    from bcn.codes import SHEET_CODES
    from bcn.runner import passed
    err = lambda code: {"code": code, "level": "error"}  # noqa: E731
    assert passed({"ok": False, "diagnostics": [err("CUE_LOW_CONFIDENCE"), err("CUE_CUT_AT_BOUNDARY")]}, SHEET_CODES)
    # An error in the SRT itself still blocks, alone or alongside a slide-timing one.
    assert not passed({"ok": False, "diagnostics": [err("CUE_SRT_ORDER")]}, SHEET_CODES)
    assert not passed({"ok": False, "diagnostics": [err("CUE_LOW_CONFIDENCE"), err("CUE_SRT_PAST_END")]}, SHEET_CODES)
    assert not passed({"ok": False, "diagnostics": []}, SHEET_CODES)
    assert not passed({"ok": False, "diagnostics": [err("CUE_LOW_CONFIDENCE")]})


def test_full_compose_requires_cues_to_pass(tree, capsys):
    """--draft accepts a doubtful cue sheet (watching it is how it gets fixed); a full compose
    needs cues to have passed, and to have run with the video in."""
    t = tree / "KV7015/U01/T01"
    from bcn.markdown import parse
    p = parse(t / "topic.md")
    blocks, time_ = [], 0.5
    for s in p.slides:
        for sentence in [x.strip() + "." for x in s.say_text.split(".") if x.strip()]:
            a, b = time_, time_ + len(sentence.split()) / 2.5
            blocks.append(f"{len(blocks) + 1}\n00:{int(a // 60):02d}:{a % 60:06.3f} --> 00:{int(b // 60):02d}:{b % 60:06.3f}\n{sentence}\n".replace(".", ",", 2))
            time_ = b + 0.1
    (t / "edit").mkdir()
    (t / "edit/master.srt").write_text("\n".join(blocks))
    for f in ("topic.md", "edit/master.srt"):
        age(t / f, 30)
    bcn(capsys, "validate", str(t))
    code, env = bcn(capsys, "cues", str(t))
    assert code == 0, env["diagnostics"]
    make_video(t / "edit/master.mp4", time_ + 2)
    age(t / "edit/master.mp4", 60)   # older than the cue sheet, so only video_checked catches it

    def compose_error(*extra):
        _, env = bcn(capsys, "compose", str(t), *extra)
        return next(d for d in env["diagnostics"] if d["level"] == "error")

    assert "before the video was in" in compose_error()["message"]

    step = t / "build/cues.json"
    m = step.stat().st_mtime
    env = json.loads(step.read_text())
    env["ok"] = False
    env["results"][0]["video_checked"] = True
    env["diagnostics"].append({"code": "CUE_LOW_CONFIDENCE", "level": "error", "message": "x"})
    step.write_text(json.dumps(env))
    import os
    os.utime(step, (m, m))
    assert "cues run failed" in compose_error()["message"]
    # --draft gets past the cue sheet and stops at the next thing it needs (render was never run).
    assert "render" in compose_error("--draft")["message"]
