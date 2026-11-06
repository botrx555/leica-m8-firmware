#!/usr/bin/env python3
"""
Turn an M8 14-bit .RAW dump into a DNG the way the camera would (docs/DNG_PLAN.md §3):

  1. overwrite the first 8 bytes of the 108-byte RAW header with a TIFF header
     pointing at IFD0 in the trailing sensor row;
  2. leave every image pixel where it is (StripOffsets = 108, width 3976 incl. padding);
  3. copy a constant ~900-byte template (IFD0 + EXIF IFD + values), with the per-shot
     "slots" in SHOT_FIELDS patched, over the start of the trailing row (sensor row 2647,
     outside the image strip). File size is unchanged.

In camera (FINDINGS "Image save path"): step 1 = header code at 0x3CE48; step 3 = hook in
the RAW producer's last-block branch (0x3C6A8) copying the template into the DMA buffer
before it is copied to the card.

The template is the exact blob the firmware would carry; -t writes it out with its slot
offsets.

Rule: this is the proof of concept for the in-camera writer, so it does only what the
camera can do -- constant tags, a few per-shot values the camera already knows, no pixel
processing and no per-frame analysis. Per-shot values come from the camera's metadata struct S
(job+4, FINDINGS "Per-shot metadata"); on the desktop they are read from the same-stem .JPG's
EXIF (the camera writes that JPG from S), else date = RAW mtime and the rest = camera defaults.
AsShotNeutral = fixed daylight (later a WB-preset table); BlackLevel = constant 92 (later scaled
by ISO gain, which the camera knows).

Usage: python3 tools/m8_wrapdng.py in.RAW [out.DNG] [--black N (default 92, 0 = omit)] [-t template.bin]
       [--meta: as `dng --meta`, the Leica MakerNote + AsShotNeutral from the JPG's MakerNote]
       [--play: --meta in PLAY's layout (build_play_template), small RGB image from the JPG]
"""
import os, struct, sys, time

RAW_SIZE = 21_049_052                  # 54 header u16 + 2647 sensor rows x 3976
HDR = 108
W, H, W_ACTIVE = 3976, 2646, 3968
STRIP = W * H * 2
BASE = HDR + STRIP                     # trailing row (7,952 bytes): IFD0 offset
TRAIL = RAW_SIZE - BASE

# Tag values copied from a 2.014 in-camera DNG (L1030602.DNG); matrices as the camera writes them.
MAKE, MODEL, UNIQUE, SOFTWARE = 'Leica Camera AG', 'M8 Digital Camera', 'M8 Digital Camera', '2.014'
CM1 = [10469, -5314, 1280, -4326, 12176, 2419, -886, 2473, 7160]      # /10000, Std A
CM2 = [7675, -2195, -305, -5860, 14118, 1857, -2425, 4007, 6578]      # /10000, D65
DAYLIGHT_NEUTRAL = [(16384, 31315), (16384, 16384), (16384, 20850)]   # m8raw2dng2 default
BLACK = 92                             # sensor black pedestal at ISO 160 (m8raw2dng2 base value)
# Higher ISOs are the base reading x 2^n digitally, pedestal included (FINDINGS "Black level per
# ISO"): black = BLACK x gain, gain = 2^round(log2(ISO/160)) as in m8raw2dng / m8raw2dng2. Unknown
# ISO (0) or anything below the first threshold -> gain 1; capped at gain 16 (ISO 2500, the M8's
# highest). The firmware hook carries the same thresholds (m8_dngpatch.black_table()).
BLACK_ISO_STEPS = (226, 452, 905, 1810)   # ISO above each -> one more doubling (exact rounding bounds)


def black_for_iso(iso, base=BLACK):
    gain = 1
    for t in BLACK_ISO_STEPS:
        if (iso or 0) > t:
            gain *= 2
    return base * gain


BYTE, ASCII, SHORT, LONG, RATIONAL, UNDEF, SLONG, SRATIONAL = 1, 2, 3, 4, 5, 7, 9, 10
SIZE = {BYTE: 1, ASCII: 1, SHORT: 2, LONG: 4, RATIONAL: 8, UNDEF: 1, SLONG: 4, SRATIONAL: 8}
FMT = {BYTE: 'B', SHORT: 'H', LONG: 'I', SLONG: 'i'}

DT0 = '0000:00:00 00:00:00'
# Per-shot slots: (tag, name, type, default, where the camera holds it). Defaults as the camera's
# metadata init (0x6DF5C). In camera each slot is filled by copying from S; offsets per FINDINGS.
SHOT_FIELDS = [
    (306,   'DateTime',          ASCII,     DT0,             'S+0x1C'),
    (33434, 'ExposureTime',      RATIONAL,  [(0, 1)],        'S+0x64'),
    (34850, 'ExposureProgram',   SHORT,     0,               'S+0x60'),
    (34855, 'ISO',               SHORT,     100,             'S+0x62'),
    (36867, 'DateTimeOriginal',  ASCII,     DT0,             'S+0x1C'),
    (36868, 'DateTimeDigitized', ASCII,     DT0,             'S+0x1C'),
    (37377, 'ShutterSpeedValue', SRATIONAL, [(0, 65536)],    'S+0x6C'),
    (37380, 'ExposureBias',      SRATIONAL, [(0, 65536)],    'S+0x8C'),
    (37381, 'MaxAperture',       RATIONAL,  [(0, 65536)],    'S+0x94'),
    (37386, 'FocalLength',       RATIONAL,  [(0, 1000)],     'S+0xA8'),
    (50728, 'AsShotNeutral',     RATIONAL,  DAYLIGHT_NEUTRAL, 'WB preset table'),
]
SLOT_TAGS = {tag: (name, typ) for tag, name, typ, _, _ in SHOT_FIELDS}
EXIF_TAGS = {33434, 34850, 34855, 36864, 36867, 36868, 37377, 37380, 37381, 37386, 37500}

# `--meta` (FINDINGS "Per-shot white balance"): the Leica MakerNote as the camera writes it into its
# JPG (0x6D400): "LEICA\0\0\0", 18 entries, value offsets counted from MakerNote start + 8.
# (tag, type, S offset, bytes copied from S; the rest of a value stays 0.)
MAKER_FIELDS = [
    (0x300, SHORT, 0xC6, 1), (0x302, LONG, 0xBC, 1), (0x303, LONG, 0xC8, 4),      # 0x303 body serial
    (0x304, SHORT, 0xA4, 2), (0x310, LONG, 0x9C, 4),                               # 0x310 lens code
    (0x311, RATIONAL, 0x84, 8), (0x312, RATIONAL, 0x7C, 8), (0x313, RATIONAL, 0x74, 8),   # meter values
    (0x320, SLONG, 0x134, 4), (0x321, LONG, 0xC0, 4),
    (0x322, RATIONAL, 0xCC, 8), (0x323, RATIONAL, 0xD4, 8), (0x324, RATIONAL, 0xDC, 8),   # per-shot WB
    (0x330, LONG, 0x138, 4), (0x331, LONG, 0x13C, 4), (0x332, LONG, 0x140, 4), (0x333, LONG, 0x144, 4),
    (0x340, LONG, 0x18, 4)]
NEUTRAL_S = 0xCC        # AsShotNeutral = the camera's neutral, S+0xCC (3 rationals 16384/x), if no word is 0


def maker_blob():
    """(MakerNote bytes, [(offset in the MakerNote, S offset, length)]): the per-shot copies. The
    rationals are stored in S order, so 0x311-0x313 and 0x322-0x324 are one copy each."""
    n = len(MAKER_FIELDS)
    head = 8 + 2 + 12 * n + 4
    rats = sorted((s, tag) for tag, typ, s, _ in MAKER_FIELDS if typ == RATIONAL)
    at = {tag: head + 8 * i for i, (_, tag) in enumerate(rats)}
    blob, copies = bytearray(b'LEICA\0\0\0' + struct.pack('<H', n)), []
    for tag, typ, s, nb in MAKER_FIELDS:
        if typ == RATIONAL:
            blob += struct.pack('<HHII', tag, typ, 1, at[tag] - 8)
        else:
            copies.append((len(blob) + 8, s, nb))
            blob += struct.pack('<HHII', tag, typ, 1, 0)
    blob += bytes(4 + 8 * len(rats))
    for i, (s, _) in enumerate(rats):                   # merge rationals contiguous in S
        if i and copies[-1][1] + copies[-1][2] == s and copies[-1][0] + copies[-1][2] == head + 8 * i:
            copies[-1] = (copies[-1][0], copies[-1][1], copies[-1][2] + 8)
        else:
            copies.append((head + 8 * i, s, 8))
    return bytes(blob), copies


def pack_value(typ, v):
    if typ == ASCII:
        return v.encode('latin1') + b'\0'
    if typ in (BYTE, UNDEF) and isinstance(v, bytes):
        return v
    if typ in (RATIONAL, SRATIONAL):
        return b''.join(struct.pack('<ii' if typ == SRATIONAL else '<II', *r) for r in v)
    v = v if isinstance(v, (list, tuple)) else [v]
    return struct.pack('<%d%s' % (len(v), FMT[typ]), *v)


def build_template(black=BLACK, maker=False):
    """Return (template bytes, {slot name: (offset in template, length, type)}); maker=True adds
    the Leica MakerNote to the EXIF IFD (slot 'MakerNote')."""
    shot = [(tag, typ, default) for tag, _, typ, default, _ in SHOT_FIELDS]
    exif = [(36864, UNDEF, b'0220')] + [e for e in shot if e[0] in EXIF_TAGS]   # ExifVersion + slots
    if maker:
        exif.append((37500, UNDEF, maker_blob()[0]))
    ifd0 = [
        (254, LONG, 0),                                          # main image = raw
        (256, LONG, W), (257, LONG, H),
        (258, SHORT, 16), (259, SHORT, 1), (262, SHORT, 32803),  # 16-bit, uncompressed, CFA
        (271, ASCII, MAKE), (272, ASCII, MODEL),
        (273, LONG, HDR), (274, SHORT, 1), (277, SHORT, 1),
        (278, LONG, H), (279, LONG, STRIP), (284, SHORT, 1),
        (305, ASCII, SOFTWARE),
        (33421, SHORT, [2, 2]), (33422, BYTE, b'\x00\x01\x01\x02'),   # RGGB
        (34665, LONG, 0),                                        # ExifIFD, fixed up below
        (50706, BYTE, b'\x01\x00\x00\x00'), (50707, BYTE, b'\x01\x00\x00\x00'),
        (50708, ASCII, UNIQUE),
        *([(50714, SHORT, black)] if black else []),
        (50717, LONG, 16383),
        (50719, SHORT, [2, 2]), (50720, SHORT, [W_ACTIVE - 4, H - 4]),  # hides padding + 2px edge
        (50721, SRATIONAL, [(x, 10000) for x in CM1]),
        (50722, SRATIONAL, [(x, 10000) for x in CM2]),
        (50731, RATIONAL, [(1, 1)]), (50732, RATIONAL, [(1, 1)]),  # BaselineNoise/Sharpness
        (50733, LONG, 500), (50738, RATIONAL, [(0, 1)]),         # BayerGreenSplit, AntiAlias (none)
        (50778, SHORT, 17), (50779, SHORT, 21),
        *[e for e in shot if e[0] not in EXIF_TAGS],             # DateTime, AsShotNeutral slots
    ]
    ifd0_len = 2 + 12 * len(ifd0) + 4
    exif_off = ifd0_len
    data_off = exif_off + 2 + 12 * len(exif) + 4
    out, data, slots = bytearray(), bytearray(), {}

    def emit(entries, next_ifd=0):
        blob = struct.pack('<H', len(entries))
        for tag, typ, v in sorted(entries):
            if tag == 34665:
                v = BASE + exif_off
            raw = pack_value(typ, v)
            count = len(raw) // SIZE[typ]
            if len(raw) <= 4:
                field = raw.ljust(4, b'\0')
                where = len(out) + len(blob) + 8
            else:
                where = data_off + len(data)
                field = struct.pack('<I', BASE + where)
                data.extend(raw + b'\0' * (len(raw) & 1))       # keep word alignment
            if tag in SLOT_TAGS:
                name, _ = SLOT_TAGS[tag]
                slots[name] = (where, len(raw), typ)
            elif tag == 50714:                                  # per-ISO black level (computed)
                slots['BlackLevel'] = (where, len(raw), typ)
            elif tag == 37500:
                slots['MakerNote'] = (where, len(raw), typ)
            blob += struct.pack('<HHI', tag, typ, count) + field
        return blob + struct.pack('<I', next_ifd)

    out += emit(ifd0)
    assert len(out) == exif_off
    out += emit(exif)
    assert len(out) == data_off
    return bytes(out + data), slots


# `--play` (FINDINGS "How PLAY reads a DNG"): the layout the camera's own DNG reader needs, so PLAY
# can show the file named .DNG: IFD0 = small 8-bit RGB image (every 8th pixel of every 8th row of
# the camera's 320x240 review image) + SubIFDs + ExifIFD + the DNG colour tags; SubIFD = the raw
# (same pixels, same tags); EXIF + WhiteBalance (required by PLAY's EXIF reader 0x6CC60).
THUMB_W, THUMB_H, THUMB_STEP = 40, 30, 8
REVIEW_W, REVIEW_H = 320, 240


def build_play_template(black=BLACK):
    """Return (template, slots) for `--play`: as build_template(black, maker=True) but in PLAY's
    layout; slots['Thumbnail'] = (offset after the template, THUMB_W * THUMB_H * 3, None)."""
    shot = [(tag, typ, default) for tag, _, typ, default, _ in SHOT_FIELDS]
    exif = [(36864, UNDEF, b'0220')] + [e for e in shot if e[0] in EXIF_TAGS]
    exif += [(37500, UNDEF, maker_blob()[0]), (41987, SHORT, 0)]          # MakerNote, WhiteBalance
    ifd0 = [
        (254, LONG, 1), (256, LONG, THUMB_W), (257, LONG, THUMB_H), (258, SHORT, [8, 8, 8]),
        (259, SHORT, 1), (262, SHORT, 2), (271, ASCII, MAKE), (272, ASCII, MODEL),
        (273, LONG, 'THUMB'), (274, SHORT, 1), (277, SHORT, 3), (278, LONG, THUMB_H),
        (279, LONG, THUMB_W * THUMB_H * 3), (284, SHORT, 1), (305, ASCII, SOFTWARE),
        (330, LONG, 'RAW'), (34665, LONG, 'EXIF'),
        (50706, BYTE, b'\x01\x00\x00\x00'), (50707, BYTE, b'\x01\x00\x00\x00'), (50708, ASCII, UNIQUE),
        (50721, SRATIONAL, [(x, 10000) for x in CM1]), (50722, SRATIONAL, [(x, 10000) for x in CM2]),
        (50731, RATIONAL, [(1, 1)]), (50732, RATIONAL, [(1, 1)]), (50778, SHORT, 17), (50779, SHORT, 21),
        *[e for e in shot if e[0] not in EXIF_TAGS],             # DateTime, AsShotNeutral slots
    ]
    raw = [
        (254, LONG, 0), (256, LONG, W), (257, LONG, H), (258, SHORT, 16), (259, SHORT, 1),
        (262, SHORT, 32803), (273, LONG, HDR), (277, SHORT, 1), (278, LONG, H), (279, LONG, STRIP),
        (284, SHORT, 1), (33421, SHORT, [2, 2]), (33422, BYTE, b'\x00\x01\x01\x02'),
        *([(50714, SHORT, black)] if black else []), (50717, LONG, 16383),
        (50719, SHORT, [2, 2]), (50720, SHORT, [W_ACTIVE - 4, H - 4]),
        (50733, LONG, 500), (50738, RATIONAL, [(0, 1)]),
    ]
    size = lambda e: 2 + 12 * len(e) + 4
    off = {'RAW': size(ifd0)}
    off['EXIF'] = off['RAW'] + size(raw)
    data_off = off['EXIF'] + size(exif)
    out, data, slots, fix = bytearray(), bytearray(), {}, []

    def emit(entries):
        blob = struct.pack('<H', len(entries))
        for tag, typ, v in sorted(entries, key=lambda e: e[0]):
            if v == 'THUMB':
                fix.append(len(out) + len(blob) + 8)
                v = 0
            elif typ == LONG and v in off:
                v = BASE + off[v]
            raw_v = pack_value(typ, v)
            count = len(raw_v) // SIZE[typ]
            if len(raw_v) <= 4:
                field = raw_v.ljust(4, b'\0')
                where = len(out) + len(blob) + 8
            else:
                where = data_off + len(data)
                field = struct.pack('<I', BASE + where)
                data.extend(raw_v + b'\0' * (len(raw_v) & 1))
            if tag in SLOT_TAGS:
                slots[SLOT_TAGS[tag][0]] = (where, len(raw_v), typ)
            elif tag == 50714:
                slots['BlackLevel'] = (where, len(raw_v), typ)
            elif tag == 37500:
                slots['MakerNote'] = (where, len(raw_v), typ)
            blob += struct.pack('<HHI', tag, typ, count) + field
        return blob + struct.pack('<I', 0)

    out += emit(ifd0)
    assert len(out) == off['RAW']
    out += emit(raw)
    assert len(out) == off['EXIF']
    out += emit(exif)
    assert len(out) == data_off
    t = out + data
    t += bytes(-len(t) % 4)
    thumb = len(t)
    for at in fix:
        struct.pack_into('<I', t, at, BASE + thumb)
    slots['Thumbnail'] = (thumb, THUMB_W * THUMB_H * 3, None)
    return bytes(t), slots


def thumbnail(review):
    """The camera's IFD0 image from a 320x240 R,G,B review image: every THUMB_STEP-th pixel and row."""
    return b''.join(review[3 * (REVIEW_W * y * THUMB_STEP + x * THUMB_STEP):][:3]
                    for y in range(THUMB_H) for x in range(THUMB_W))


def wrap(raw, template, slots, shot):
    """What the camera does per shot: header, slot patches (from S), template into trailing row."""
    assert len(raw) == RAW_SIZE, f'RAW is {len(raw)} bytes, expected {RAW_SIZE}'
    t = bytearray(template)
    for name, value in shot.items():
        off, n, typ = slots[name]
        v = pack_value(typ, value)
        assert len(v) == n, f'{name}: {len(v)} bytes, slot holds {n}'
        t[off:off + n] = v
    assert len(t) <= TRAIL
    return b'II*\0' + struct.pack('<I', BASE) + raw[8:BASE] + bytes(t) + raw[BASE + len(t):]


def check(dng, raw):
    """Structural self-check: re-read the TIFF and confirm pixels are untouched."""
    assert dng[:4] == b'II*\0' and struct.unpack_from('<I', dng, 4)[0] == BASE
    assert len(dng) == RAW_SIZE, 'file size changed'
    assert dng[8:BASE] == raw[8:BASE], 'pixel/header bytes changed'
    n = struct.unpack_from('<H', dng, BASE)[0]
    tags = {}
    for i in range(n):
        tag, typ, cnt, val = struct.unpack_from('<HHII', dng, BASE + 2 + 12 * i)
        tags[tag] = (typ, cnt, val)
    assert list(tags) == sorted(tags), 'tags not ascending'
    if 330 in tags:                                     # --play: IFD0 = RGB image, raw in the SubIFD
        sub = tags[330][2]
        assert tags[258][1] == 3 and tags[262][2] == 2 and tags[277][2] == 3
        assert tags[273][2] + tags[279][2] <= len(dng)
        rt = {t: (ty, c, v) for t, ty, c, v in
              (struct.unpack_from('<HHII', dng, sub + 2 + 12 * i) for i in range(struct.unpack_from('<H', dng, sub)[0]))}
        assert rt[273][2] == HDR and rt[279][2] == STRIP and rt[256][2] == W and rt[257][2] == H
        return n
    assert tags[273][2] == HDR and tags[279][2] == STRIP and HDR + STRIP <= RAW_SIZE
    assert tags[256][2] == W and tags[257][2] == H
    for tag, (typ, cnt, val) in tags.items():
        if SIZE[typ] * cnt > 4:
            assert val % 2 == 0 and val + SIZE[typ] * cnt <= len(dng), f'tag {tag} out of range'
    return n


def read_maker(buf, off):
    """{tag: (type, value bytes)} of the Leica MakerNote at buf[off] (offsets from off + 8)."""
    assert buf[off:off + 8] == b'LEICA\0\0\0', 'no LEICA MakerNote'
    out = {}
    for k in range(struct.unpack_from('<H', buf, off + 8)[0]):
        e = off + 10 + 12 * k
        tag, typ, cnt, val = struct.unpack_from('<HHII', buf, e)
        n = SIZE[typ] * cnt
        out[tag] = (typ, bytes(buf[e + 8:e + 8 + n] if n <= 4 else buf[off + 8 + val:off + 8 + val + n]))
    return out


def tiff_maker(t):
    """The Leica MakerNote of a little-endian TIFF (IFD0 -> EXIF IFD -> 0x927C), or {}."""
    def entries(off):
        return [struct.unpack_from('<HHII', t, off + 2 + 12 * k) for k in range(struct.unpack_from('<H', t, off)[0])]
    exif = [v for tag, _, _, v in entries(struct.unpack_from('<I', t, 4)[0]) if tag == 34665]
    mn = [v for tag, _, _, v in entries(exif[0]) if tag == 37500] if exif else []
    return read_maker(t, mn[0]) if mn else {}


def fill_maker(template, slots, maker):
    """Template with the MakerNote values and AsShotNeutral of `maker` ({tag: (type, bytes)}, e.g.
    from the camera's JPG) -- placed by reading the template's own MakerNote entries."""
    t = bytearray(template)
    off = slots['MakerNote'][0]
    for k in range(struct.unpack_from('<H', t, off + 8)[0]):
        e = off + 10 + 12 * k
        tag, typ, cnt, val = struct.unpack_from('<HHII', t, e)
        v = maker[tag][1]
        assert maker[tag][0] == typ and len(v) == SIZE[typ] * cnt, f'MakerNote 0x{tag:X}'
        at = e + 8 if len(v) <= 4 else off + 8 + val
        t[at:at + len(v)] = v
    wb = b''.join(maker[tag][1] for tag in (0x322, 0x323, 0x324))
    if all(struct.unpack('<6I', wb)):
        w, n, _ = slots['AsShotNeutral']
        t[w:w + n] = wb
    return bytes(t)


def jpg_tiff(path):
    """The TIFF data of a JPG's Exif APP1, or None."""
    d = open(path, 'rb').read()
    i = 2
    while i + 4 < len(d) and d[i] == 0xFF:
        n = struct.unpack('>H', d[i + 2:i + 4])[0]
        if d[i + 1] == 0xE1 and d[i + 4:i + 10] == b'Exif\0\0':
            return d[i + 10:i + 2 + n]
        i += 2 + n
    return None


def jpg_shot(path):
    """Desktop stand-in for S: the per-shot slot values from the camera's own JPG EXIF."""
    t = jpg_tiff(path)
    if t is None:
        return {}
    e = '<' if t[:2] == b'II' else '>'
    want = {tag: name for tag, (name, _) in SLOT_TAGS.items()}
    out = {}

    def ifd(off):
        for k in range(struct.unpack_from(e + 'H', t, off)[0]):
            tag, typ, cnt, val = struct.unpack_from(e + 'HHII', t, off + 2 + 12 * k)
            voff = off + 2 + 12 * k + 8
            if tag == 34665:
                ifd(val)
            elif tag in want:
                if typ == ASCII:
                    out[want[tag]] = t[val:val + cnt].split(b'\0')[0].decode('latin1')
                elif typ == SHORT:
                    out[want[tag]] = struct.unpack_from(e + 'H', t, voff)[0]
                elif typ in (RATIONAL, SRATIONAL):
                    out[want[tag]] = [struct.unpack_from(e + ('ii' if typ == SRATIONAL else 'II'), t, val)]
    ifd(struct.unpack_from(e + 'I', t, 4)[0])
    return out


def main():
    args = [a for a in sys.argv[1:]]
    black, tpl_out = BLACK, None
    if '--black' in args:
        i = args.index('--black'); black = int(args[i + 1]); del args[i:i + 2]
    if '-t' in args:
        i = args.index('-t'); tpl_out = args[i + 1]; del args[i:i + 2]
    play = '--play' in args
    meta = '--meta' in args or play
    args = [a for a in args if a not in ('--meta', '--play')]
    if not args:
        raise SystemExit(__doc__)
    src = args[0]
    dst = args[1] if len(args) > 1 else os.path.splitext(src)[0] + '.DNG'
    raw = open(src, 'rb').read()
    template, slots = build_play_template(black) if play else build_template(black, maker=meta)
    jpg = os.path.splitext(src)[0] + '.JPG'
    shot = jpg_shot(jpg) if os.path.exists(jpg) else {}
    if meta:
        if not os.path.exists(jpg):
            raise SystemExit('--meta needs the same-stem .JPG (MakerNote values)')
        template = fill_maker(template, slots, tiff_maker(jpg_tiff(jpg)))
    if 'DateTime' not in shot:   # no JPG: RAW mtime stands in for the camera clock
        when = time.strftime('%Y:%m:%d %H:%M:%S', time.localtime(os.path.getmtime(src)))
        shot.update(DateTime=when, DateTimeOriginal=when, DateTimeDigitized=when)
    if black:
        shot['BlackLevel'] = black_for_iso(shot.get('ISO'), black)
    dng = wrap(raw, template, slots, shot)
    if play:                                    # the camera samples its 320x240 review image
        from PIL import Image
        off, n, _ = slots['Thumbnail']
        review = Image.open(jpg).convert('RGB').resize((REVIEW_W, REVIEW_H), Image.BOX).tobytes()
        dng = dng[:BASE + off] + thumbnail(review) + dng[BASE + off + n:]
    n = check(dng, raw)
    open(dst, 'wb').write(dng)
    print(f'template {len(template)} bytes, {n} IFD0 tags, {len(slots)} slots; per-shot values from '
          + (f'{os.path.basename(jpg)}: ' + ', '.join(f'{k}={v}' for k, v in shot.items()) if os.path.exists(jpg)
             else 'defaults + RAW mtime'))
    print(f'wrote {dst} ({len(dng)} bytes, same as RAW); changed: bytes 0-7 and '
          f'{len(template)} of {TRAIL} trailing-row bytes')
    if tpl_out:
        open(tpl_out, 'wb').write(template)
        print('template written to', tpl_out)


if __name__ == '__main__':
    main()
