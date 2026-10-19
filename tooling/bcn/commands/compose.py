"""bcn compose: the delivered video. Slides composited against the presenter, with the
topic's bumpers baked in, at the partner's delivery quality. This is what package copies
into out/.

Subtitles are not burned in by default: a sidecar SRT is written instead, its timings
shifted by the intro's duration so it lines up with the composed file's own timeline.
edit/master.srt itself is never touched, and cues.csv keeps the master's own timing.

--draft trades all of that for a small, fast, watermarked, burned-in-subtitle file for
checking cue timing quickly: never delivered, never confused with the real thing.

Bumpers never change the cue sheet: the body is composed on its own timeline, where
cues.csv is correct as written, and the intro and outro are joined around it afterwards.
The intro's duration is reported as body_offset, in the envelope and in the delivered
manifest, so the sidecar SRT's shift is always traceable to a real number. The topic's
own bumpers from bcn bumpers are used when current, else the files named in [bumpers].
"""

from __future__ import annotations

import argparse
import contextlib
import html
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .. import cuesheet, fsutil, srt, storage, tools
from ..config import Config, Theme, load_theme, resolve_theme_name
from ..coursemap import load_course_map, speaker_for
from ..envelope import Artifact, Diagnostic, Envelope, Fail, TopicResult, sha256_file
from ..markdown import ParsedTopic, parse, to_html
from ..media import ffmpeg, loudness, probe
from ..progress import TopicProgress, run as run_proc
from ..runner import require_input, require_step, run_topics, try_skip
from ..tree import Target, Topic

HELP = "composite video: what package delivers"

FONT_CANDIDATES = ["/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]

IMG_TAG_RE = re.compile(r"<img\b[^>]*>")


ARRANGEMENTS = ("side_by_side", "inset", "stacked", "full_bleed_inset", "full_bleed_text")


@dataclass
class EncodeSpec:
    """What compose actually builds to, for one layout: that layout's own width/height
    (scaled down for --draft, keeping its aspect ratio), [delivery]'s fps/codec/bitrate, and
    [compose]'s draft_* quality knobs under --draft."""
    width: int
    height: int
    fps: int
    watermark: str
    video_codec: str
    audio_codec: str
    crf: int
    preset: str
    bitrate: tuple[str, str] | None  # (video, audio); None in draft mode, which uses crf instead


def _draft_dims(width: int, height: int, budget: int = 960) -> tuple[int, int]:
    """width/height scaled so the longer edge is `budget`, keeping the layout's own aspect
    ratio and orientation (a 1920x1080 widescreen layout drafts at 960x540; a 1080x1920
    vertical layout drafts at 540x960) rather than a single fixed draft frame size."""
    scale = budget / max(width, height)
    return _even(width * scale), _even(height * scale)


def encode_spec(cfg: Config, layout: dict, draft: bool) -> EncodeSpec:
    c = cfg["compose"]
    if draft:
        w, h = _draft_dims(layout["width"], layout["height"])
        return EncodeSpec(w, h, c["draft_fps"], c["draft_watermark"],
                          "h264", "aac", c["draft_crf"], c["draft_preset"], bitrate=None)
    d = cfg["delivery"]
    return EncodeSpec(layout["width"], layout["height"], d["fps"], "", d["video_codec"], d["audio_codec"],
                      crf=18, preset="slow", bitrate=(d["video_bitrate"], d["audio_bitrate"]))


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--no-bumpers", action="store_true", help="skip intro and outro, for iterating on timings")
    p.add_argument("--draft", action="store_true",
                   help="a small, fast, watermarked file with subtitles burned in, for checking cue timing; "
                        "never delivered")
    p.add_argument("--burn-subtitles", action="store_true",
                   help="burn subtitles into the video instead of writing a sidecar SRT")
    p.add_argument("--layout", action="append", default=None,
                   help="a layout name from [compose.layouts] to build (repeatable); "
                        "default is [compose].default_layouts")


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def _presenter_band_height(c: dict, H: int) -> int:
    """The presenter's share of a stacked layout's frame height, shared by filter_graph and
    name_tag_box so the two never disagree about where the band ends."""
    return _even(H * c["split_ratio"])


def _esc(s: str) -> str:
    """Escape a value for use inside an ffmpeg filter argument."""
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace(",", "\\,")


def watermark_logo_html(theme: Theme, width: int, height: int) -> str:
    """A page the exact size of the watermark itself, transparent, the theme's logo filling
    it. Unlike bumpers' outro card (a full slide-sized frame with the logo centred in it), this
    page has no frame of its own to crop out: card.mjs can screenshot it directly."""
    logo = theme.asset(theme.bumper["logo"]) if theme.bumper else None
    img = f"<img src='{logo.as_uri()}' alt=''>" if logo else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
html, body {{ margin: 0; width: {width}px; height: {height}px; overflow: hidden; background: transparent; }}
img {{ display: block; width: 100%; height: 100%; object-fit: contain; }}
</style></head><body>{img}</body></html>
"""


@dataclass
class NameTag:
    """Where and when the "who is speaking" tag is overlaid, on the body's own timeline."""
    input: str    # ffmpeg input label of the transparent tag PNG, e.g. "3:v"
    x: int
    y: int
    start: float
    hold: float   # from the start of the fade in to the end of the fade out
    fade: float


def name_tag_box(c: dict, es: EncodeSpec, theme: Theme) -> tuple[int, int, int, int, int]:
    """(x, y, width, height, name font px) of the tag at the encode size.

    The area spans the presenter's share in side_by_side (the half of the frame on the
    aligned side for inset and full_bleed_inset, or the presenter's own band for stacked),
    with its bottom edge clear of the subtitle safe area (or, for stacked, clear of the band's
    own bottom — there is no slide-with-subtitles below it to avoid). The tag's box sits at
    its left or right per [name_tag] align, so "right" is against the frame's right edge,
    inset by the margin — except full_bleed_inset, which instead anchors to the side opposite
    the slide's inset_position, so the tag and the slide PiP box cannot land in the same
    corner. The logo watermark lives inside the safe area at the bottom right, so the two
    never meet.
    """
    W, H = _even(es.width), _even(es.height)
    k = H / theme.height
    nt = theme.name_tag or {}
    m = _even(H * 0.03)
    arrangement = c["arrangement"]

    def sized(w: int) -> tuple[int, int]:
        """(font, h) for a box this wide. An explicit [name_tag].font_size_px is still
        height-relative, per its own documented "percent of frame height" contract in
        theme.toml — a theme author's deliberate choice, left alone. The default instead
        scales with the box's own width rather than a flat frame-relative constant: a fixed
        fraction of frame width looked fine at 1920 (side_by_side's own column) but was
        either far too large relative to a narrow box (truncating names) or, over-corrected,
        far too small. Scaling off the box itself tracks whichever width that box actually
        has — including the 80% of the frame the non-side_by_side arrangements below can
        now use if a long name needs it.
        """
        font = round(nt.get("font_size_px") * k) if nt.get("font_size_px") else round(w * 0.05)
        return font, _even(font * 2.6)

    if arrangement == "side_by_side":
        left = W - _even(W * theme.safe_right)
        x, w = left + m, (W - left) - 2 * m
        font, h = sized(w)
        y = H - round(theme.safe_bottom * k) - h - m
    elif arrangement == "inset":
        w = _even(W * 0.8)
        font, h = sized(w)
        x = W - w - m if nt.get("align", "right") == "right" else m
        y = H - round(theme.safe_bottom * k) - h - m
    elif arrangement == "stacked":
        w = _even(W * 0.8)
        font, h = sized(w)
        ph = _presenter_band_height(c, H)
        x = W - w - m if nt.get("align", "right") == "right" else m
        y = ph - h - m
    elif arrangement == "full_bleed_inset":
        w = _even(W * 0.8)
        font, h = sized(w)
        x = m if "right" in c["inset_position"] else W - w - m
        y = H - round(theme.safe_bottom * k) - h - m
    elif arrangement == "full_bleed_text":
        # Hard against the right edge regardless of [name_tag] align: this arrangement's
        # own fixed convention, not a theme choice (the slide text already owns the centre).
        # The bottom still clears the subtitle safe area, same as every other arrangement.
        w = _even(W * 0.8)
        font, h = sized(w)
        x = W - w
        y = H - round(theme.safe_bottom * k) - h - m
    else:
        raise Fail("CONFIG_INVALID", f"compose arrangement '{arrangement}' is not one of {', '.join(ARRANGEMENTS)}.",
                   file="programme.toml")
    return x, y, _even(w), h, font


def name_tag_html(theme: Theme, lang: str, name: str, role: str, width: int, height: int, font: int) -> str:
    """A transparent page the size of the tag area: a box at its left or right ([name_tag]
    align) holding the name over the role line, in the theme's fonts, with the accent bar on
    that same side. A name too long for the area is cut with an ellipsis."""
    nt = theme.name_tag or {}
    side = nt.get("align", "right")
    faces = "".join(
        f"@font-face {{ font-family: \"{f['family']}\"; src: url(\"{(theme.dir / f['file']).as_uri()}\"); font-weight: {f.get('weight', 400)}; }}\n"
        for f in theme.font_faces or [])
    stack = ", ".join(f'"{f}"' if f not in ("sans-serif", "serif") else f for f in theme.fonts[lang])
    pad = round(font * 0.35)
    role_html = f"<div class='role'>{html.escape(role)}</div>" if role else ""
    return f"""<!doctype html><html lang="{'zh-Hans' if lang == 'zh' else 'en'}"><head><meta charset="utf-8"><style>
{faces}
html, body {{ margin: 0; width: {width}px; height: {height}px; overflow: hidden; background: transparent; }}
body {{ display: flex; align-items: flex-end; justify-content: {'flex-end' if side == 'right' else 'flex-start'}; }}
.tag {{ box-sizing: border-box; max-width: 100%; padding: {pad}px {round(pad * 1.6)}px; background: {nt.get('background')};
        border-{side}: {max(3, round(font * 0.18))}px solid {nt.get('accent')}; font-family: {stack}; text-align: {side}; }}
.tag div {{ white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.name {{ font-size: {font}px; line-height: 1.15; font-weight: {theme.title_weight}; color: {nt.get('color')}; }}
.role {{ font-size: {round(font * 0.62)}px; line-height: 1.3; font-weight: 400; color: {nt.get('role_color')}; }}
</style></head><body><div class="tag"><div class="name">{html.escape(name)}</div>{role_html}</div></body></html>
"""


def slide_text_font_range(theme: Theme, lang: str, H: int) -> tuple[int, int]:
    """(max, min) px for slide_text_html's #title, card.mjs shrinks within this range to
    fit. Scaled from the theme's own declared content size, like name_tag_box's k factor,
    so a longer slide starts big and steps down rather than every slide sharing one size.
    Deliberately well under the theme's own content size: this is a caption over video, not
    a full slide deck filling the frame."""
    base = theme.content_font_size.get(lang) or 44
    k = H / theme.height
    return _even(base * k * 0.6), _even(base * k * 0.3)


def text_band(height: int, safe_bottom: int) -> tuple[int, int]:
    """(top, height) of full_bleed_text's text band: the middle 30% of the frame, pulled up
    if that would otherwise dip into the subtitle safe area (safe_bottom, theme.safe_bottom
    already scaled to this frame). Shared by slide_text_html (which renders a page only this
    tall, not the whole frame — a full-frame transparent canvas composited for the whole
    video duration was the one thing in full_bleed_text's filter graph with no equivalent
    anywhere else in bcn's ffmpeg usage, everything else overlays a bounded region) and
    filter_graph (which positions that smaller overlay at the same y).
    """
    band_height = round(height * 0.30)
    band_top = round(height * 0.5 - band_height / 2)
    max_bottom = height - safe_bottom - round(height * 0.02)
    if band_top + band_height > max_bottom:
        band_top = max(0, max_bottom - band_height)
    return band_top, band_height


def slide_text_html(theme: Theme, lang: str, content_html: str, width: int, band_height: int) -> str:
    """A transparent page the width of the frame but only as tall as the text band itself
    (text_band's band_height) — not the whole frame, which would be mostly empty alpha=0
    padding composited for no reason. The slide's own text (heading, bullets, paragraphs —
    images already stripped by the caller), with no background panel behind it — full_bleed_
    text's whole point is the slide image is gone, not replaced by another box. Legible over
    arbitrary video via a drop shadow instead of a fill. #title inside #box lets card.mjs
    shrink long slides to fit rather than overflow or clip.
    """
    faces = "".join(
        f"@font-face {{ font-family: \"{f['family']}\"; src: url(\"{(theme.dir / f['file']).as_uri()}\"); font-weight: {f.get('weight', 400)}; }}\n"
        for f in theme.font_faces or [])
    stack = ", ".join(f'"{f}"' if f not in ("sans-serif", "serif") else f for f in theme.fonts[lang])
    side_margin = round(width * 0.06)
    return f"""<!doctype html><html lang="{'zh-Hans' if lang == 'zh' else 'en'}"><head><meta charset="utf-8"><style>
{faces}
html, body {{ margin: 0; width: {width}px; height: {band_height}px; overflow: hidden; background: transparent; }}
body {{ box-sizing: border-box; padding: 0 {side_margin}px; display: flex; align-items: center; justify-content: center; }}
#box {{ max-height: {band_height}px; width: 100%; overflow: hidden; }}
#title {{ font-family: {stack}; color: #ffffff; text-align: center; max-width: 100%;
          text-shadow: 0 2px 14px rgba(0,0,0,0.9), 0 0 6px rgba(0,0,0,0.9); }}
#title h1, #title h2, #title h3 {{ font-weight: {theme.title_weight}; font-size: 1.3em; margin: 0 0 0.4em; line-height: 1.15; }}
#title p {{ font-size: 0.85em; margin: 0 0 0.5em; }}
#title ul, #title ol {{ font-size: 0.85em; text-align: left; display: inline-block; margin: 0.3em 0; padding-left: 1.2em; }}
#title li {{ margin: 0.25em 0; }}
</style></head><body><div id="box"><div id="title">{content_html}</div></div></body></html>
"""


def filter_graph(c: dict, es: EncodeSpec, duration: float, burn_subs: str | None, sub_font: str, sub_font_size: int,
                 watermark_font: str | None, fonts_dir: Path | None = None, safe_right: float = 0.5,
                 logo_input: str | None = None, tag: NameTag | None = None, text_band_top: int = 0) -> str:
    """burn_subs is the SRT filename to burn in, or None to leave the video plain (sidecar mode).

    c is this layout's own entry from [compose.layouts] (arrangement, inset_scale,
    inset_position, inset_margin, split_ratio).

    safe_right is the theme's safe_area.right (the fraction of the frame kept clear of slide
    content for side_by_side): the presenter fills exactly that share, full height, cropped to
    fit with no letterboxing, on the assumption the footage is already framed for it. The
    slide input (input 1) is assumed pre-rendered to match: full-bleed (no reservation) for
    every arrangement except side_by_side, which gets the safe-area-reserved render instead
    (bcn render produces both; compose_topic picks the matching one per layout).

    logo_input is the ffmpeg input label (e.g. "2:v") of a pre-rendered, transparent logo PNG
    to draw over the bottom-right corner of the whole frame, side_by_side only: a watermark on
    top of the presenter, not the small logo bumpers already put on the outro card.

    tag, if given, is the speaker's name tag: faded in and out by its alpha channel over its
    time window, drawn last so it sits above everything else.

    text_band_top is full_bleed_text's own input 1 (its text overlay) y position: that input
    is already cropped to text_band's band height, not the full frame (see slide_text_html),
    so it only needs placing, not scaling.
    """
    W, H, fps = _even(es.width), _even(es.height), es.fps
    parts = []
    arrangement = c["arrangement"]
    if arrangement == "side_by_side":
        sw = W - _even(W * safe_right)  # where the presenter's share starts: the theme's safe_right
        pw = W - sw                     # the presenter's share: exactly the width the theme reserves
        # The slide PNG is already the full 1920x1080 frame with its content confined to the
        # left by the theme's own safe_area.right (bcn render enforces this), so it is used as
        # the full-size base layer directly, with only the presenter needing any scaling.
        parts.append(f"[1:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps={fps},setsar=1[slides]")
        parts.append(f"[0:v]scale={pw}:{H}:force_original_aspect_ratio=increase,crop={pw}:{H},fps={fps},setsar=1[pres]")
        parts.append(f"[slides][pres]overlay=x={sw}:y=0:eof_action=repeat[base]")
        if logo_input:
            m = _even(H * 0.03)
            parts.append(f"[base][{logo_input}]overlay=x=W-w-{m}:y=H-h-{m}[base1]")
            base = "base1"
        else:
            base = "base"
    elif arrangement == "inset":
        iw = _even(W * c["inset_scale"])
        m = c["inset_margin"]
        pos = c["inset_position"]
        x = f"W-w-{m}" if "right" in pos else str(m)
        y = f"H-h-{m}" if "bottom" in pos else str(m)
        parts.append(f"[1:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps={fps},setsar=1[slides]")
        parts.append(f"[0:v]scale={iw}:-2,fps={fps},setsar=1[pres]")
        parts.append(f"[slides][pres]overlay=x={x}:y={y}:eof_action=repeat[base]")
        base = "base"
    elif arrangement == "stacked":
        # The presenter's band (top, full width) over the slide's band (the rest), each
        # produced with the idioms already used above: force-fill-and-crop for the presenter
        # (same as side_by_side's column, just full width instead of a share of it), scale-
        # and-pad for the slide (same as every arrangement's full-frame slide base, just onto
        # a band instead of the whole frame).
        ph = _presenter_band_height(c, H)
        sh = H - ph
        parts.append(f"[0:v]scale={W}:{ph}:force_original_aspect_ratio=increase,crop={W}:{ph},fps={fps},setsar=1[pres]")
        parts.append(f"[1:v]scale={W}:{sh}:force_original_aspect_ratio=decrease,pad={W}:{sh}:(ow-iw)/2:(oh-ih)/2,fps={fps},setsar=1[slides]")
        parts.append("[pres][slides]vstack=inputs=2[base]")
        base = "base"
    elif arrangement == "full_bleed_inset":
        # inset's mirror image: the presenter fills the whole frame (cropped to fit, same as
        # side_by_side's column but full-frame) and the slide becomes the small inset box.
        iw = _even(W * c["inset_scale"])
        m = c["inset_margin"]
        pos = c["inset_position"]
        x = f"W-w-{m}" if "right" in pos else str(m)
        y = f"H-h-{m}" if "bottom" in pos else str(m)
        parts.append(f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={fps},setsar=1[base0]")
        parts.append(f"[1:v]scale={iw}:-2,fps={fps},setsar=1[slidepip]")
        parts.append(f"[base0][slidepip]overlay=x={x}:y={y}:eof_action=repeat[base]")
        base = "base"
    elif arrangement == "full_bleed_text":
        # Presenter fills the whole frame, same as full_bleed_inset; input 1 is not a
        # rendered slide image here but a transparent per-slide text PNG — cropped to just
        # the text band's own height (see text_band/slide_text_html), not the full frame, so
        # it is only placed at text_band_top, not scaled. A full-frame transparent overlay
        # composited for the whole video duration would be the one thing in this filter
        # graph with no bounded-region equivalent anywhere else in bcn's ffmpeg usage.
        parts.append(f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={fps},setsar=1[base0]")
        if tag:
            # The name tag and the slide's centred text would otherwise land in the same
            # area early on: hold the text at alpha=0 until the tag's own hold ends, then
            # fade it in over the tag's own fade duration, rather than showing both at once.
            reveal_at = tag.start + tag.hold
            parts.append(f"[1:v]format=rgba,fps={fps},fade=t=in:st={reveal_at:.3f}:d={tag.fade:.3f}:alpha=1[textov]")
        else:
            parts.append(f"[1:v]format=rgba,fps={fps}[textov]")
        parts.append(f"[base0][textov]overlay=x=0:y={text_band_top}:eof_action=repeat[base]")
        base = "base"
    else:
        raise Fail("CONFIG_INVALID", f"compose arrangement '{arrangement}' is not one of {', '.join(ARRANGEMENTS)}.",
                   file="programme.toml")
    if tag:
        out_at = tag.start + tag.hold - tag.fade
        parts.append(f"[{tag.input}]format=rgba,fade=t=in:st={tag.start:.3f}:d={tag.fade:.3f}:alpha=1,"
                     f"fade=t=out:st={out_at:.3f}:d={tag.fade:.3f}:alpha=1[tag]")
        parts.append(f"[{base}][tag]overlay=x={tag.x}:y={tag.y}:eof_action=pass[comp]")
    else:
        parts.append(f"[{base}]null[comp]")
    draw = ""
    if es.watermark:
        ff = f"fontfile='{_esc(watermark_font)}':" if watermark_font else ""
        draw = (f",drawtext={ff}text='{_esc(es.watermark)}':x=w-tw-{_even(W * 0.02)}:y=h-th-{_even(H * 0.02)}:"
                f"fontsize={_even(H * 0.06)}:fontcolor=white@0.85:box=1:boxcolor=0xc0392b@0.75:boxborderw={_even(H * 0.012)}")
    if burn_subs:
        style = f"FontName={sub_font},FontSize={sub_font_size},Outline=1,Shadow=0,MarginV=12"
        fd = f":fontsdir='{_esc(str(fonts_dir))}'" if fonts_dir and fonts_dir.is_dir() else ""
        parts.append(f"[comp]subtitles=filename='{_esc(burn_subs)}'{fd}:force_style='{_esc(style)}'{draw},format=yuv420p[v]")
    else:
        parts.append(f"[comp]null{draw},format=yuv420p[v]" if draw else "[comp]format=yuv420p[v]")
    return ";".join(parts)


def _encode_args(es: EncodeSpec) -> list[str]:
    quality = ["-b:v", es.bitrate[0]] if es.bitrate else ["-crf", str(es.crf)]
    audio_bitrate = es.bitrate[1] if es.bitrate else "128k"
    vcodec = "libx264" if es.video_codec == "h264" else es.video_codec
    return ["-c:v", vcodec, "-preset", es.preset, *quality, "-pix_fmt", "yuv420p", "-r", str(es.fps),
            "-c:a", es.audio_codec, "-b:a", audio_bitrate, "-ar", "48000", "-ac", "2", "-movflags", "+faststart"]


def _shift_srt(subs: Path, offset: float) -> str:
    """subs shifted forward by offset, for the sidecar delivered beside the composed video.

    edit/master.srt (or the prepared subtitle file) is only ever read here, never written:
    this returns text for a new file. If offset is 0 the cues are unchanged in value, only
    reformatted, which is harmless (SRT_CORRECTION-style tools compare parsed cues, not bytes).
    """
    cues = srt.parse_file(subs, str(subs.name))
    shifted = [srt.Cue(c.index, c.start + offset, c.end + offset, c.text, c.line) for c in cues]
    return srt.to_srt(shifted)


def compose_topic(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config, args: argparse.Namespace) -> None:
    with contextlib.ExitStack() as stack:
        _compose(t, r, tp, cfg, args, stack)


def _compose(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config, args: argparse.Namespace,
             stack: contextlib.ExitStack) -> None:
    lang = args.lang
    c = cfg["compose"]
    draft = args.draft
    burn_in = draft or args.burn_subtitles

    names = list(dict.fromkeys(args.layout or c["default_layouts"]))
    unknown = [n for n in names if n not in c["layouts"]]
    if unknown:
        raise Fail("CONFIG_INVALID", f"Unknown layout(s): {', '.join(unknown)}. See [compose.layouts] in programme.toml.",
                   file="programme.toml")
    layouts = {name: c["layouts"][name] for name in names}
    for name, layout in layouts.items():
        if layout["arrangement"] not in ARRANGEMENTS:
            raise Fail("CONFIG_INVALID",
                       f"compose.layouts.{name}.arrangement '{layout['arrangement']}' is not one of {', '.join(ARRANGEMENTS)}.",
                       file="programme.toml")

    require_input(t, t.video, stable=True)
    if not t.cues_csv.is_file():
        raise Fail("STEP_PREREQUISITE", "There is no cue sheet.", topic=t.id, hint="Run bcn cues first.")
    # A draft is how a low-confidence cue sheet gets checked, so compose accepts a
    # cue sheet whose cues run failed, as long as it is current.
    if not fsutil.is_fresh([t.cues_csv], [t.src("en"), t.srt("en"), t.video]):
        raise Fail("STEP_PREREQUISITE", "The cue sheet is older than the script, SRT or video.", hint="Run bcn cues again.")
    require_step(t, "render", lang, [t.src(lang)], f"bcn render{' --lang ' + lang if lang != 'en' else ''}")
    p: ParsedTopic = parse(t.src(lang), t.src(lang).name, t.id)
    n = len(p.slides)

    # Each layout needs one of two pre-rendered slide-content renders: the safe-area-
    # reserved one, for side_by_side (the presenter's column), or the full-bleed one, for
    # inset/stacked/full_bleed_inset (the presenter isn't confined to a fixed in-frame
    # column there). bcn render produces both; resolve whichever set(s) the selected layouts
    # actually need. full_bleed_text needs neither: its "slide" track is the slide's own
    # text, rendered fresh per layout further down, not a pre-rendered slide image at all.
    modes_needed = sorted({layout["arrangement"] != "side_by_side" for layout in layouts.values()
                           if layout["arrangement"] != "full_bleed_text"})
    pngs_by_mode: dict[bool, list[Path]] = {}
    for full_bleed in modes_needed:
        pngs = [t.slides_dir(lang, full_bleed) / t.slide_name(i, lang) for i in range(1, n + 1)]
        for png in pngs:
            require_input(t, png, lang, step_hint=f"bcn render{' --lang ' + lang if lang != 'en' else ''}")
        pngs_by_mode[full_bleed] = pngs

    subs = t.subtitle_out(lang, "srt")
    if not subs.is_file():
        subs = t.srt(lang)
    require_input(t, subs, lang)

    # The topic's own bumpers (bcn bumpers) come first; the programme-wide files in [bumpers] are the fallback.
    bumpers: dict[str, Path] = {}
    if not args.no_bumpers:
        for kind in ("intro", "outro"):
            made = t.bumper(kind, lang)
            if made.is_file():
                if fsutil.is_fresh([made], [t.src(lang)]):
                    bumpers[kind] = made
                    continue
                r.diagnostics.append(Diagnostic("COMPOSE_BUMPER", f"The topic's {kind} is older than {t.src(lang).name}; it is skipped.",
                                                topic=t.id, lang=lang, file=str(made.relative_to(t.dir)),
                                                hint=f"Run bcn bumpers{' --lang ' + lang if lang != 'en' else ''} again."))
            rel = cfg["bumpers"][kind]
            if rel:
                bumper_path = (t.root / rel).resolve()
                if not str(bumper_path).startswith(str(t.root.resolve())) or not bumper_path.is_file():
                    r.diagnostics.append(Diagnostic("COMPOSE_BUMPER", f"The {kind} bumper '{rel}' does not exist inside the programme root; it is skipped.",
                                                    topic=t.id, lang=lang, file="programme.toml"))
                else:
                    bumpers[kind] = bumper_path

    outputs = [(t.draft(lang, name) if draft else t.composed(lang, name)) for name in layouts]
    sidecar = None if (draft or burn_in) else t.composed_srt(lang)
    if sidecar:
        outputs.append(sidecar)
    theme = load_theme(t.root, resolve_theme_name(cfg, args.theme))
    course_map = t.module_dir / "course-map.md"
    speaker = speaker_for(load_course_map(t.module_dir), t.unit, lang)
    all_pngs = [p for pngs in pngs_by_mode.values() for p in pngs]
    # t.src(lang) itself, not just the rendered pngs: full_bleed_text builds its slide track
    # straight from the topic's text, and if it's the only layout selected, modes_needed is
    # empty and all_pngs has nothing in it to catch a content change via mtime instead.
    inputs = [t.video, t.cues_csv, subs, t.src(lang), t.root / "programme.toml",
              *([course_map] if course_map.is_file() else []), *theme.files(), *all_pngs, *bumpers.values()]
    if try_skip(t, r, "compose", lang, outputs, inputs, args.force):
        return

    # A watermark of the theme's logo over the presenter, side_by_side only: drawn on top of
    # everything, unlike bumpers' outro card, which sits on its own separate slide.
    want_logo_any = (bool(theme.bumper) and bool(theme.bumper.get("logo"))
                     and any(layout["arrangement"] == "side_by_side" for layout in layouts.values()))
    want_text_any = any(layout["arrangement"] == "full_bleed_text" for layout in layouts.values())
    tl = tools.require(cfg, "ffmpeg", "ffprobe", *(["chrome"] if (want_logo_any or speaker or want_text_any) else []))
    times = cuesheet.read(t.cues_csv, n, t.cues_csv.name)
    # The master may live in the bucket: bring it down for this job only. The scratch copy is
    # removed when `stack` unwinds in compose_topic, whether this succeeds or fails.
    video = stack.enter_context(storage.workspace(t.root, [t.video], t.build))[t.video]
    info = probe(tl, video, "edit/master.mp4")
    duration = info.duration
    if times[-1] >= duration:
        raise Fail("CUE_SHEET_INVALID", "The last slide starts after the video ends.", file=t.cues_csv.name)
    if not speaker:
        r.diagnostics.append(Diagnostic("COMPOSE_NO_SPEAKER", "The course map names no speaker, so the video has no name tag.",
                                        topic=t.id, lang=lang, file=f"{t.module}/course-map.md",
                                        hint="Add a line like '**Speaker.** Dr Jane Smith, Associate Professor' above the first "
                                             "unit (or inside a unit, for that unit only)."))
    body_lufs = loudness(tl, video) if bumpers else None

    sub_font = theme.subtitle_font(lang)
    wm_font = next((f for f in FONT_CANDIDATES if Path(f).is_file()), None)

    work = t.build / f".compose-{lang}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    body_offset = 0.0
    layout_results: list[dict] = []
    try:
        if burn_in:
            shutil.copyfile(subs, work / "subs.srt")

        for idx, (name, layout) in enumerate(layouts.items()):
            es = encode_spec(cfg, layout, draft)
            text_band_top = 0  # full_bleed_text only; everything else overlays at y=0 or doesn't use this at all
            slice_lo, slice_hi = idx / len(layouts), (idx + 1) / len(layouts)

            def report(frac: float, label: str) -> None:
                tp.update(min(99.0, (slice_lo + frac * (slice_hi - slice_lo)) * 100), f"{label} {name}")

            if layout["arrangement"] == "full_bleed_text":
                # No pre-rendered slide image here: each slide's own text (heading/bullets,
                # images stripped), rendered fresh as a transparent PNG exactly the size of
                # this layout's frame — the same card.mjs screenshot technique already used
                # for the logo watermark and name tag, but batched into one Chrome launch for
                # every slide instead of relaunching per slide: this is the only place in bcn
                # where the number of Chrome launches would otherwise scale with slide count
                # rather than staying a small fixed number per command, and on a long topic
                # that was enough repeated launch/teardown overhead to risk an OOM kill.
                W_t, H_t = _even(es.width), _even(es.height)
                maxF, minF = slide_text_font_range(theme, lang, H_t)
                safe_bottom_px = round(theme.safe_bottom * (H_t / theme.height))
                text_band_top, band_height = text_band(H_t, safe_bottom_px)
                text_dir = work / f"text-{name}"
                text_dir.mkdir(exist_ok=True)
                pngs = []
                manifest = []
                for i, slide in enumerate(p.slides, 1):
                    content_html = IMG_TAG_RE.sub("", to_html(slide.content))
                    html_path, png_path = text_dir / f"s{i:02d}.html", text_dir / f"s{i:02d}.png"
                    html_path.write_text(slide_text_html(theme, lang, content_html, W_t, band_height), encoding="utf-8")
                    manifest.append({"in": str(html_path), "out": str(png_path), "w": W_t, "h": band_height,
                                     "maxF": maxF, "minF": minF, "mode": "transparent"})
                    pngs.append(png_path)
                manifest_path = text_dir / "manifest.json"
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                text_code, _text_out, text_err = run_proc(
                    [tl.node or "node", str(tools.NODE_DIR / "card.mjs"), "--batch", str(manifest_path)],
                    env={"CHROME_PATH": tl.chrome or ""}, cwd=str(tools.NODE_DIR), timeout=180 + 10 * n)
                missing = [i for i, png in enumerate(pngs, 1) if not png.is_file()]
                if text_code != 0 or missing:
                    raise Fail("RENDER_FAILED", f"Rendering the slide text overlays failed (exit {text_code}).",
                               topic=t.id, lang=lang, data={"stderr": text_err[-2000:], "missing_slides": missing})
            else:
                full_bleed = layout["arrangement"] != "side_by_side"
                pngs = pngs_by_mode[full_bleed]

            # Slide track on the body's own timeline: cues.csv is correct as written here.
            lines = ["ffconcat version 1.0"]
            for i, png in enumerate(pngs):
                end = times[i + 1] if i + 1 < len(times) else duration
                lines += [f"file '{png.as_posix()}'", f"duration {end - times[i]:.3f}"]
            lines.append(f"file '{pngs[-1].as_posix()}'")
            (work / f"slides-{name}.ffconcat").write_text("\n".join(lines) + "\n")

            if layout["arrangement"] == "full_bleed_text":
                # ffmpeg's concat demuxer switching between many distinct alpha PNGs, inside
                # a filter graph that has to alpha-blend them (not a plain opaque paste),
                # accumulates memory on every switch instead of staying bounded — confirmed
                # by direct reproduction: identical filter graph, identical total duration,
                # one continuous source stayed flat around 300MB while fifteen switches of
                # the very same image climbed past 1.5GB. (Every other arrangement's slide
                # track is also a many-file concat, but its PNGs are opaque, so overlay does
                # a cheap replace rather than a real per-pixel blend — that combination is
                # what's unique to full_bleed_text.) Collapse the per-slide PNGs into one
                # continuous alpha video first, in a small separate pass — qtrle is a native
                # ffmpeg codec (no external library, always built in) with real alpha
                # support — so the expensive main encode below never sees a multi-file
                # concat for this track at all.
                text_track = work / f"text-track-{name}.mov"
                ffmpeg(tl, ["-f", "concat", "-safe", "0", "-i", f"slides-{name}.ffconcat",
                            "-vf", f"format=rgba,fps={es.fps}", "-c:v", "qtrle", "-pix_fmt", "argb", str(text_track)],
                       duration=duration, cwd=str(work))
                slide_input_args = ["-i", str(text_track)]
            else:
                slide_input_args = ["-f", "concat", "-safe", "0", "-i", f"slides-{name}.ffconcat"]

            # A watermark of the theme's logo over the presenter, side_by_side only.
            want_logo = layout["arrangement"] == "side_by_side" and want_logo_any
            logo_args: list[str] = []
            logo_input = None
            if want_logo:
                # A small page sized to the watermark itself (unlike the outro card, which is a
                # full 1920x1080 frame with the logo centred in it): the image just fills it.
                logo_h = _even(_even(es.height) * 0.08)
                logo_w = _even(logo_h * 3)
                (work / f"logo-{name}.html").write_text(watermark_logo_html(theme, logo_w, logo_h), encoding="utf-8")
                logo_code, _logo_out, logo_err = run_proc(
                    [tl.node or "node", str(tools.NODE_DIR / "card.mjs"), str(work / f"logo-{name}.html"),
                     str(work / f"logo-{name}.png"), str(logo_w), str(logo_h), "1000", "1000", "transparent"],
                    env={"CHROME_PATH": tl.chrome or ""}, cwd=str(tools.NODE_DIR), timeout=180)
                if logo_code != 0 or not (work / f"logo-{name}.png").is_file():
                    r.diagnostics.append(Diagnostic("COMPOSE_BUMPER", f"Rendering the logo watermark failed (exit {logo_code}); composing {name} without it.",
                                                    topic=t.id, lang=lang, data={"stderr": logo_err[-500:]}))
                else:
                    logo_args = ["-i", str(work / f"logo-{name}.png")]
                    logo_input = "2:v"

            # The speaker's name tag over the first seconds of the body.
            tag_args: list[str] = []
            tag = None
            if speaker:
                tx, ty, tw, th, tfont = name_tag_box(layout, es, theme)
                (work / f"tag-{name}.html").write_text(name_tag_html(theme, lang, *speaker, tw, th, tfont), encoding="utf-8")
                tag_code, _tag_out, tag_err = run_proc(
                    [tl.node or "node", str(tools.NODE_DIR / "card.mjs"), str(work / f"tag-{name}.html"),
                     str(work / f"tag-{name}.png"), str(tw), str(th), "1000", "1000", "transparent"],
                    env={"CHROME_PATH": tl.chrome or ""}, cwd=str(tools.NODE_DIR), timeout=180)
                if tag_code != 0 or not (work / f"tag-{name}.png").is_file():
                    r.diagnostics.append(Diagnostic("COMPOSE_NAME_TAG", f"Rendering the name tag failed (exit {tag_code}); composing {name} without it.",
                                                    topic=t.id, lang=lang, data={"stderr": tag_err[-500:]}))
                else:
                    nt = theme.name_tag
                    tag_args = ["-loop", "1", "-framerate", str(es.fps), "-t", f"{duration:.3f}", "-i", str(work / f"tag-{name}.png")]
                    tag = NameTag(f"{2 + (1 if logo_args else 0)}:v", tx, ty, nt["start_seconds"], nt["hold_seconds"], nt["fade_seconds"])

            share = 0.85 if bumpers else 1.0
            body = work / f"body-{name}.mp4"
            graph = filter_graph(layout, es, duration, "subs.srt" if burn_in else None, sub_font, c["subtitle_font_size"],
                                 wm_font, theme.fonts_dir, theme.safe_right, logo_input, tag, text_band_top)
            report(0, "encoding")
            ffmpeg(tl, ["-i", str(video), *slide_input_args, *logo_args, *tag_args,
                        "-filter_complex", graph, "-map", "[v]", "-map", "0:a?", "-t", f"{duration:.3f}",
                        *_encode_args(es), str(body)],
                   duration=duration, on_pct=lambda pct: report(pct / 100 * share, "encoding"), cwd=str(work))

            if bumpers:
                report(share, "bumpers")
                segs = []
                W, H, fps = _even(es.width), _even(es.height), es.fps
                for kind in ("intro", "outro"):
                    if kind not in bumpers:
                        continue
                    src = bumpers[kind]
                    bi = probe(tl, src, str(src.relative_to(t.root)))
                    seg = work / f"{kind}-{name}.mp4"
                    vf = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps={fps},setsar=1,format=yuv420p"
                    a_in = [] if bi.has_audio else ["-f", "lavfi", "-t", f"{bi.duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
                    af = f"loudnorm=I={body_lufs:.1f}:TP=-1.5:LRA=11,aresample=48000" if (body_lufs is not None and bi.has_audio) else "aresample=48000"
                    amap = "0:a" if bi.has_audio else "1:a"
                    ffmpeg(tl, ["-i", str(src), *a_in, "-vf", vf, "-af", af, "-map", "0:v", "-map", amap,
                                "-t", f"{bi.duration:.3f}", *_encode_args(es), str(seg)], duration=bi.duration)
                    if kind == "intro" and idx == 0:
                        body_offset = bi.duration
                    segs.append((kind, seg))
                order = [s for k, s in segs if k == "intro"] + [body] + [s for k, s in segs if k == "outro"]
                (work / f"join-{name}.ffconcat").write_text("ffconcat version 1.0\n" + "".join(f"file '{p.name}'\n" for p in order))
                joined = work / f"joined-{name}.mp4"
                ffmpeg(tl, ["-f", "concat", "-safe", "0", "-i", f"join-{name}.ffconcat", "-c", "copy", "-movflags", "+faststart", str(joined)],
                       duration=None, cwd=str(work))
                body = joined

            out = t.draft(lang, name) if draft else t.composed(lang, name)
            kind = "draft" if draft else "composed"
            if storage.remote(t.root) is not None:
                # The bucket is the home of the finished video; hash it here, where the bytes are,
                # then upload and leave a pointer in the tree. `body` is consumed by the upload.
                sha = sha256_file(body)
                r.artifacts.append(Artifact(str(out.relative_to(t.root)), kind, body.stat().st_size, sha))
                tp.update(98, f"uploading {out.name}")
                storage.publish(t.root, body, out, dur=probe(tl, body, out.name).duration, sha256=sha)
            else:
                with fsutil.atomic_path(out) as tmp:
                    shutil.move(str(body), tmp)
                r.artifacts.append(Envelope.artifact_for(t.root, out, kind))
            layout_results.append({"name": name, "arrangement": layout["arrangement"], "resolution": [_even(es.width), _even(es.height)]})

        if sidecar:
            with fsutil.atomic_path(sidecar) as tmp:
                Path(tmp).write_text(_shift_srt(subs, body_offset), encoding="utf-8")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    if sidecar:
        r.artifacts.append(Envelope.artifact_for(t.root, sidecar, "composed_subtitles"))
    r.extra.update({"mode": "draft" if draft else "full", "subtitles": "burned" if burn_in else "sidecar",
                    "layouts": layout_results,
                    "body_offset": round(body_offset, 3), "bumpers": sorted(bumpers), "duration": round(duration, 3),
                    "name_tag": speaker[0] if speaker else None})
    tp.update(100, "done")


def run(args: argparse.Namespace, env: Envelope, target: Target) -> None:
    def fn(t: Topic, r: TopicResult, tp: TopicProgress, cfg: Config) -> None:
        compose_topic(t, r, tp, cfg, args)

    run_topics(env, target, "compose", args.lang, fn, jobs=args.jobs)
