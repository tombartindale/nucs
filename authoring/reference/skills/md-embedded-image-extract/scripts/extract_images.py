#!/usr/bin/env python3
"""Replace base64-embedded images in a topic Markdown file with file paths and
deliver a zip laid out as  KV####/U##/T##/assets/fig-<num>.<ext>.

Usage:
  extract_images.py INPUT.md [--zip OUT.zip] [--topic-id KV7015-U03-T06]
                    [--link-prefix assets] [--dry-run]

The module/unit/topic folders come from the front-matter `topic_id`
(e.g. KV7015-U03-T06 -> KV7015/U03/T06/); pass --topic-id if it is absent.
The zip contains the cleaned Markdown, named <topic_id>.md (upload names like
topic__1_.md are mangled; use --keep-name to keep the input name), in the T## folder
and the decoded images in its assets/ folder, numbered from 1 in document
order (fig-1.png, fig-2.png...). Links are relative to the Markdown file:
assets/fig-<num>.<ext> (override with --link-prefix). The input file is never modified. Every PNG is integrity-checked (chunk CRCs,
IEND, inflate) before anything is written; a corrupt image aborts the run.
Alt text and everything else in the Markdown is preserved byte for byte.
"""
import argparse, base64, binascii, os, re, struct, sys, zipfile, zlib

PATTERN = re.compile(
    r'!\[(?P<alt>(?:[^\[\]\\]|\\.|\[[^\]]*\])*)\]\(\s*data:(?P<mime>image/[A-Za-z0-9.+-]+)'
    r'(?P<params>(?:;[A-Za-z0-9=._-]+)*?);base64,(?P<data>[A-Za-z0-9+/=\s_-]+?)\s*'
    r'(?P<title>\s+"[^"]*")?\)',
    re.S)
MAGIC = [(b'\x89PNG\r\n\x1a\n', 'png'), (b'\xff\xd8\xff', 'jpg'),
         (b'GIF87a', 'gif'), (b'GIF89a', 'gif'), (b'RIFF', 'webp')]
MIME_EXT = {'png': 'png', 'jpeg': 'jpg', 'jpg': 'jpg', 'gif': 'gif',
            'webp': 'webp', 'svg+xml': 'svg', 'bmp': 'bmp'}
ID_RE = re.compile(r'^(?P<kv>KV\d+)-(?P<u>U\d+)-(?P<t>T\d+)$', re.I)


def ext_for(mime, raw):
    for sig, ext in MAGIC:
        if raw.startswith(sig):
            if ext == 'webp' and raw[8:12] != b'WEBP':
                continue
            return ext
    if b'<svg' in raw[:200]:
        return 'svg'
    return MIME_EXT.get(mime.split('/', 1)[1].lower(), 'bin')


def png_problem(raw):
    """Return None if raw is a structurally valid PNG (every chunk CRC matches,
    IDAT inflates, IEND present); otherwise a short description of the fault."""
    if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'missing PNG signature'
    pos, idat, seen_end = 8, b'', False
    while pos + 8 <= len(raw):
        n, typ = struct.unpack('>I4s', raw[pos:pos + 8])
        body, crc = raw[pos + 8:pos + 8 + n], raw[pos + 8 + n:pos + 12 + n]
        if len(body) < n or len(crc) < 4:
            return f'truncated in {typ.decode("latin1")} chunk'
        if zlib.crc32(typ + body) & 0xffffffff != struct.unpack('>I', crc)[0]:
            return f'CRC mismatch in {typ.decode("latin1")} chunk (data corrupted)'
        if typ == b'IDAT':
            idat += body
        if typ == b'IEND':
            seen_end = True
            break
        pos += 12 + n
    if not seen_end:
        return 'no IEND chunk (truncated)'
    try:
        zlib.decompress(idat)
    except zlib.error as e:
        return f'image data does not inflate ({e})'
    return None


def topic_id_from(text):
    m = re.match(r'---\s*\n(.*?)\n---', text, re.S)
    t = m and re.search(r'^topic_id:\s*(\S+)', m.group(1), re.M)
    return t.group(1).strip('"\'') if t else None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input')
    ap.add_argument('--zip')
    ap.add_argument('--topic-id')
    ap.add_argument('--link-prefix', default='assets')
    ap.add_argument('--keep-name', action='store_true',
                    help='keep the input file name instead of <topic_id>.md')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    with open(a.input, 'r', encoding='utf-8', newline='') as f:
        text = f.read()
    matches = list(PATTERN.finditer(text))
    if not matches:
        print('No base64-embedded images found; nothing to do.')
        return 0

    tid = a.topic_id or topic_id_from(text)
    m = ID_RE.match(tid or '')
    if not m:
        sys.exit(f'Need a topic id like KV7015-U03-T06 (front-matter topic_id '
                 f'or --topic-id); got {tid!r}.')
    folder = '/'.join(m.group(g).upper() for g in ('kv', 'u', 't'))
    link_dir = a.link_prefix.rstrip('/')

    pieces, last, images = [], 0, []
    for i, mt in enumerate(matches, 1):
        try:
            raw = base64.b64decode(re.sub(r'\s+', '', mt['data']))
        except binascii.Error as e:
            sys.exit(f'Image {i}: could not decode base64 ({e}); nothing written.')
        if len(raw) < 16:
            sys.exit(f'Image {i}: decoded data implausibly small; nothing written.')
        ext = ext_for(mt["mime"], raw)
        if ext == 'png':
            bad = png_problem(raw)
            if bad:
                sys.exit(f'Image {i}: corrupted PNG - {bad}. Nothing written.')
        name = f'fig-{i}.{ext}'
        pieces += [text[last:mt.start()],
                   f'![{mt["alt"]}]({link_dir}/{name}{mt["title"] or ""})']
        last = mt.end()
        images.append((name, raw, len(mt.group(0))))
    pieces.append(text[last:])
    new_text = ''.join(pieces)
    assert not PATTERN.search(new_text)

    out = a.zip or f'{tid.upper()}.zip'
    md_name = os.path.basename(a.input) if a.keep_name else f'{tid.upper()}.md'
    entries = [f'{folder}/{md_name}'] + [f'{folder}/assets/{n}' for n, _, _ in images]
    print('\n'.join(entries))
    if a.dry_run:
        print('Dry run: no zip written.')
        return 0
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(entries[0], new_text.encode('utf-8'))
        for (n, raw, _), e in zip(images, entries[1:]):
            z.writestr(e, raw)
    print(f'Wrote {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
