"""bcn package: assemble the delivery folder out/.

The delivered video is compose's output, not the raw editor export: slides, presenter
and bumpers composited at the partner's delivery quality (bcn compose §). The subtitle
file delivered beside it is compose's sidecar, its timings already shifted to match. The
master and cues.csv are never touched by any of this; package only ever reads them.

English builds out/ afresh in a sibling temp directory and swaps it in whole, so a
half-built package never looks complete. Mandarin composes and delivers its own video
(same presenter footage, its own slides, bumpers and subtitles) and writes its own
manifest.zh.json beside the English one. A topic's intro and outro from bcn bumpers, if
it has them, are also delivered as separate files, alongside the merged composed video.
"""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from pathlib import Path

from .. import cuesheet, fsutil, srt, storage, tools
from ..config import Config
from ..envelope import Artifact, Diagnostic, Envelope, Fail, TopicResult, sha256_file, utcnow
from ..markdown import parse
from ..media import probe
from ..progress import TopicProgress
from ..runner import require_input, require_step, run_topics, try_skip
from ..tree import DEFAULT_LAYOUT, Target, Topic

HELP = "assemble the delivery folder"


def add_args(p: argparse.ArgumentParser) -> None:
    pass


def _file_entry(p: Path) -> dict:
    """A manifest row. A delivered video that lives in the bucket is described by its pointer
    (named as the mp4, which is what consumers look for) rather than hashed from disk."""
    if p.name.endswith(storage.POINTER_SUFFIX):
        p = p.with_name(p.name[:-len(storage.POINTER_SUFFIX)])
    if not p.is_file() and (ptr := storage.read_pointer(p)):
        return {"name": p.name, "bytes": ptr["size"], "sha256": ptr.get("sha256"), "remote": True}
    return {"name": p.name, "bytes": p.stat().st_size, "sha256": sha256_file(p)}


def _artifact(root: Path, p: Path, kind: str) -> Artifact:
    if not p.is_file() and (ptr := storage.read_pointer(p)):
        return Artifact(str(p.relative_to(root)), kind, ptr["size"], ptr.get("sha256") or "")
    return Envelope.artifact_for(root, p, kind)


def _duration(tl, t: Topic, composed: Path, rel: str) -> float:
    cached = None if composed.is_file() else storage.duration(composed)
    if cached is not None:
        return cached
    with storage.workspace(t.root, [composed], t.build) as local:
        return probe(tl, local[composed], rel).duration


def _deliver_video(t: Topic, src: Path, staged: Path, final: Path | None = None) -> None:
    """Put a composed video into the delivery folder. A real file is copied; one that lives in
    the bucket is copied server-side (no download) and only its pointer is written here.
    `staged` is where it is built; `final` is where it will end up (differs for package_en,
    which swaps a staging folder in whole)."""
    final = final or staged
    in_place = final == staged  # package_zh writes into the live out/; package_en into staging
    if src.is_file():
        if in_place:
            storage.pointer_path(final).unlink(missing_ok=True)
        with fsutil.atomic_path(staged) as tmp:
            shutil.copyfile(src, tmp)
        return
    store = storage.remote(t.root)
    if store is None:
        raise Fail("FS_MISSING", f"{src.name} is not on disk and no object storage is configured.", file=str(src))
    if in_place:
        final.unlink(missing_ok=True)
    store.copy(src, final, pointer_at=staged)


def _deliver_subtitles(sidecar: Path, dest_srt: Path, fmt: str) -> list[Path]:
    """Copies compose's shifted sidecar SRT into place, plus a VTT derived from the same
    shifted cues if the delivery format calls for it. Never re-shifts anything. Each file
    is written atomically, so this is safe whether dest_srt is in fresh staging (package_en)
    or lands beside an already-live package (package_zh)."""
    with fsutil.atomic_path(dest_srt) as tmp:
        shutil.copyfile(sidecar, tmp)
    written = [dest_srt]
    if fmt in ("vtt", "both"):
        cues = srt.parse_file(sidecar, sidecar.name)
        dest_vtt = dest_srt.with_suffix(".vtt")
        fsutil.write_text(dest_vtt, srt.to_vtt(cues))
        written.append(dest_vtt)
    return written


def package_en(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config, force: bool) -> None:
    src = t.src("en")
    for p in (src, t.video, t.srt("en")):
        require_input(t, p, "en", stable=p != src)
    require_step(t, "validate", "en", [src], "bcn validate")
    render = require_step(t, "render", "en", [src], "bcn render")
    require_step(t, "cues", "en", [src, t.srt("en"), t.video], "bcn cues")
    subs_env = require_step(t, "subtitles", "en", [t.srt("en")], "bcn subtitles")
    compose_env = require_step(t, "compose", "en", [t.video, t.cues_csv, t.src("en")], "bcn compose")
    compose_result = compose_env.get("results", [{}])[0]
    if compose_result.get("mode") == "draft":
        raise Fail("STEP_PREREQUISITE", "The last compose run was --draft; that is never delivered.",
                   topic=t.id, lang="en", hint="Run bcn compose again without --draft.")

    p = parse(src, "topic.md", t.id)
    n = len(p.slides)
    pngs = t.slide_pngs("en")
    times = cuesheet.read(t.cues_csv, None, t.cues_csv.name)
    if not (len(pngs) == len(times) == n):
        raise Fail("PKG_COUNT_MISMATCH", f"Slide images: {len(pngs)}, cue sheet rows: {len(times)}, source slides: {n}. They must be equal.",
                   hint="Re-run render and cues against the current topic.md.")
    composed = t.composed("en")
    sidecar = t.composed_srt("en")
    require_input(t, composed, "en", step_hint="bcn compose")
    require_input(t, sidecar, "en", step_hint="bcn compose (without --burn-subtitles)")
    bumpers = _bumpers(t, "en")

    # Every other layout the programme actually produces ([compose].default_layouts, not the
    # full [compose.layouts] catalog -- a preset nobody asked for isn't something to deliver
    # just because someone once composed it) is still optional here: the last compose run may
    # not have selected it. Present files are delivered alongside the default layout's;
    # present-but-stale ones are excluded with a diagnostic rather than failing the whole
    # package. Freshness is judged against the true external sources only (not the default
    # layout's own composed file or the shared sidecar, which compose always finishes writing
    # last, after every layout's video — checking against those would call every layout
    # "stale" by definition).
    extra_names = [name for name in cfg["compose"]["default_layouts"] if name != DEFAULT_LAYOUT]
    extra_composed = {name: t.composed("en", name) for name in extra_names if storage.present(t.composed("en", name))}
    source_inputs = [src, t.video, t.srt("en"), t.cues_csv, *pngs, *bumpers, t.root / "programme.toml"]

    inputs = [*source_inputs, composed, sidecar, *extra_composed.values()]
    if try_skip(t, r, "package", "en", [t.manifest("en")], inputs, force):
        return

    tl = tools.require(cfg, "ffprobe")
    duration = _duration(tl, t, composed, "build/composed.mp4")
    staging = Path(tempfile.mkdtemp(prefix=".out.partial-", dir=t.dir))
    try:
        tp.update(5, "copying video")
        _deliver_video(t, composed, staging / f"{t.id}.mp4", t.out / f"{t.id}.mp4")
        default_cfg = cfg["compose"]["layouts"][DEFAULT_LAYOUT]
        delivered_layouts = [{"name": DEFAULT_LAYOUT, "video": f"{t.id}.mp4",
                              "width": default_cfg["width"], "height": default_cfg["height"]}]
        for name, extra_path in extra_composed.items():
            if not fsutil.is_fresh([extra_path], source_inputs):
                r.diagnostics.append(Diagnostic("PACKAGE_STALE_LAYOUT",
                                                f"{name} is older than its sources; it is not delivered this run.",
                                                topic=t.id, lang="en", file=str(extra_path.relative_to(t.root)),
                                                hint=f"Run bcn compose --layout {name} again."))
                continue
            dest_name = f"{t.id}.{name}.mp4"
            _deliver_video(t, extra_path, staging / dest_name, t.out / dest_name)
            layout_cfg = cfg["compose"]["layouts"][name]
            delivered_layouts.append({"name": name, "video": dest_name, "width": layout_cfg["width"], "height": layout_cfg["height"]})
        tp.update(70, "copying subtitles")
        fmt = cfg["subtitles"]["format"]
        _deliver_subtitles(sidecar, staging / f"{t.id}.en.srt", fmt)
        tp.update(85, "copying slides")
        for i, png in enumerate(pngs, 1):
            shutil.copyfile(png, staging / t.slide_name(i, "en"))
        for s in bumpers:
            shutil.copyfile(s, staging / s.name)
        shutil.copyfile(t.cues_csv, staging / t.cues_csv.name)
        (staging / f"{t.id}.md").write_text(p.stripped_file(), encoding="utf-8")

        # Carry Mandarin files across; status judges their freshness against their own manifest.
        if t.out.is_dir() and t.manifest("zh").is_file():
            zh = fsutil.read_json(t.manifest("zh")) or {}
            for f in zh.get("files", []):
                if (t.out / f["name"]).is_file() and not (staging / f["name"]).exists():
                    shutil.copyfile(t.out / f["name"], staging / f["name"])
                elif f.get("remote") and storage.read_pointer(t.out / f["name"]):
                    # Already in the bucket under its final key: carry the pointer across.
                    shutil.copyfile(storage.pointer_path(t.out / f["name"]), storage.pointer_path(staging / f["name"]))
            shutil.copyfile(t.manifest("zh"), staging / "manifest.zh.json")

        files = sorted((x for x in staging.iterdir() if x.is_file() and not x.name.startswith("manifest")),
                       key=lambda x: x.name.removesuffix(storage.POINTER_SUFFIX))
        en_files = [f for f in files if not _is_zh(f.name, t)]
        manifest = {
            "topic_id": t.id,
            "lang": "en",
            "slide_count": n,
            "duration": round(duration, 3),
            "built": utcnow(),
            "theme": render.get("results", [{}])[0].get("theme"),
            "marp_cli": render.get("results", [{}])[0].get("marp_cli"),
            "chrome": render.get("results", [{}])[0].get("chrome"),
            "subtitles_modified": subs_env.get("results", [{}])[0].get("modified", False),
            "body_offset": compose_result.get("body_offset", 0.0),
            "bumpers_composited": compose_result.get("bumpers", []),
            "layouts": delivered_layouts,
            "files": [_file_entry(f) for f in en_files],
        }
        fsutil.write_json(staging / "manifest.json", manifest)

        # Swap the finished folder in whole.
        old = None
        if t.out.exists():
            old = t.dir / f".out.old-{os.getpid()}"
            os.replace(t.out, old)
        os.replace(staging, t.out)
        if old:
            shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    for f in [t.manifest("en")] + [t.out / e["name"] for e in manifest["files"]]:
        r.artifacts.append(_artifact(t.root, f, "package"))
    r.extra.update({"slides": n, "duration": round(duration, 3), "files": len(manifest["files"]) + 1,
                    "body_offset": manifest["body_offset"]})
    tp.update(100, "done")


def _is_zh(name: str, t: Topic) -> bool:
    name = name.removesuffix(storage.POINTER_SUFFIX)
    return name.startswith(f"{t.id}-zh-") or name.endswith((".zh.srt", ".zh.vtt", ".zh.md", ".zh.mp4"))


def _bumpers(t: Topic, lang: str) -> list[Path]:
    """The topic's intro and outro, if bcn bumpers has made them; they must be current to be delivered."""
    found = [p for p in (t.bumper("intro", lang), t.bumper("outro", lang)) if p.is_file()]
    if found:
        require_step(t, "bumpers", lang, [t.src(lang)], f"bcn bumpers{' --lang ' + lang if lang != 'en' else ''}")
    return found


def package_zh(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config, force: bool) -> None:
    src = t.src("zh")
    require_input(t, src, "zh")
    require_input(t, t.srt("zh"), "zh")
    en_manifest = fsutil.read_json(t.manifest("en"))
    if not en_manifest:
        raise Fail("STEP_PREREQUISITE", "The English package does not exist.", hint="Run bcn package first.")
    require_step(t, "validate", "zh", [src, t.src("en")], "bcn validate --lang zh")
    require_step(t, "render", "zh", [src], "bcn render --lang zh")
    require_step(t, "subtitles", "zh", [t.srt("zh"), t.srt("en")], "bcn subtitles --lang zh")
    compose_env = require_step(t, "compose", "zh", [t.video, t.cues_csv, src], "bcn compose --lang zh")
    compose_result = compose_env.get("results", [{}])[0]
    if compose_result.get("mode") == "draft":
        raise Fail("STEP_PREREQUISITE", "The last compose --lang zh run was --draft; that is never delivered.",
                   topic=t.id, lang="zh", hint="Run bcn compose --lang zh again without --draft.")
    n = len(parse(src, src.name, t.id).slides)
    pngs = t.slide_pngs("zh")
    if not (len(pngs) == n == en_manifest["slide_count"]):
        raise Fail("PKG_COUNT_MISMATCH", f"Mandarin slide images: {len(pngs)}, Mandarin slides: {n}, English slides: {en_manifest['slide_count']}.")
    composed = t.composed("zh")
    sidecar = t.composed_srt("zh")
    require_input(t, composed, "zh", step_hint="bcn compose --lang zh")
    require_input(t, sidecar, "zh", step_hint="bcn compose --lang zh (without --burn-subtitles)")
    bumpers = _bumpers(t, "zh")

    extra_names = [name for name in cfg["compose"]["default_layouts"] if name != DEFAULT_LAYOUT]
    extra_composed = {name: t.composed("zh", name) for name in extra_names if storage.present(t.composed("zh", name))}
    source_inputs = [src, t.srt("zh"), *pngs, *bumpers, t.manifest("en")]

    inputs = [*source_inputs, composed, sidecar, *extra_composed.values()]
    if try_skip(t, r, "package", "zh", [t.manifest("zh")], inputs, force):
        return

    tl = tools.require(cfg, "ffprobe")
    duration = _duration(tl, t, composed, "build/composed.zh.mp4")
    written = []
    dest_video = t.out / f"{t.id}.zh.mp4"
    _deliver_video(t, composed, dest_video)
    written.append(dest_video)
    default_cfg = cfg["compose"]["layouts"][DEFAULT_LAYOUT]
    delivered_layouts = [{"name": DEFAULT_LAYOUT, "video": dest_video.name,
                          "width": default_cfg["width"], "height": default_cfg["height"]}]
    for name, extra_path in extra_composed.items():
        if not fsutil.is_fresh([extra_path], source_inputs):
            r.diagnostics.append(Diagnostic("PACKAGE_STALE_LAYOUT",
                                            f"{name} is older than its sources; it is not delivered this run.",
                                            topic=t.id, lang="zh", file=str(extra_path.relative_to(t.root)),
                                            hint=f"Run bcn compose --lang zh --layout {name} again."))
            continue
        dest = t.out / f"{t.id}.{name}.zh.mp4"
        _deliver_video(t, extra_path, dest)
        written.append(dest)
        layout_cfg = cfg["compose"]["layouts"][name]
        delivered_layouts.append({"name": name, "video": dest.name, "width": layout_cfg["width"], "height": layout_cfg["height"]})
    # Written straight into t.out, same as the slides and bumpers below: package_zh adds
    # beside an already-live package rather than replacing it whole (each file's own write
    # is still atomic).
    fmt = cfg["subtitles"]["format"]
    written.extend(_deliver_subtitles(sidecar, t.out / f"{t.id}.zh.srt", fmt))
    for i, png in enumerate(pngs, 1):
        dest = t.out / t.slide_name(i, "zh")
        with fsutil.atomic_path(dest) as tmp:
            shutil.copyfile(png, tmp)
        written.append(dest)
    for s in bumpers:
        dest = t.out / s.name
        with fsutil.atomic_path(dest) as tmp:
            shutil.copyfile(s, tmp)
        written.append(dest)
    md = t.out / f"{t.id}.zh.md"
    fsutil.write_text(md, src.read_text(encoding="utf-8"))
    written.append(md)
    manifest = {
        "topic_id": t.id,
        "lang": "zh",
        "slide_count": n,
        "duration": round(duration, 3),
        "built": utcnow(),
        "body_offset": compose_result.get("body_offset", 0.0),
        "bumpers_composited": compose_result.get("bumpers", []),
        "layouts": delivered_layouts,
        # The cue sheet is shared: Mandarin has no cues step of its own (same presenter
        # footage, same timing), so <id>.cues.csv is delivered once, by package_en.
        "shares_cue_sheet_with": "manifest.json",
        "files": [_file_entry(f) for f in written],
    }
    fsutil.write_json(t.manifest("zh"), manifest)
    for f in [t.manifest("zh"), *written]:
        r.artifacts.append(_artifact(t.root, f, "package"))
    r.extra.update({"slides": n, "files": len(written) + 1})


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    def fn(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config) -> None:
        (package_en if args.lang == "en" else package_zh)(t, r, tp, cfg, args.force)

    run_topics(env, target, "package", args.lang, fn, jobs=args.jobs)
