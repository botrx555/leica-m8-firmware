#!/usr/bin/env python3
"""
Check camera files from a `dng --meta` build (FINDINGS "Per-shot white balance"): for each .RAW
(DNG content) with its same-stem .JPG, compare the DNG's Leica MakerNote and AsShotNeutral with
the JPG's MakerNote, and read the job dump (job+0..0x280 behind the template) to show where the
values were at hook time if they differ.

Read-only. Usage: python3 tools/m8_metacheck.py fromcameraimports/L*.RAW
"""
import os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import m8_wrapdng as wd

DUMP_AT, DUMP_LEN = 0x500, 0x280        # as m8_dngpatch.DUMP_AT / DUMP_LEN


def neutral(dng):
    for k in range(struct.unpack_from('<H', dng, wd.BASE)[0]):
        tag, typ, cnt, val = struct.unpack_from('<HHII', dng, wd.BASE + 2 + 12 * k)
        if tag == 50728:
            return [struct.unpack_from('<II', dng, val + 8 * i) for i in range(cnt)]


def fmt(typ, v):
    if typ in (wd.RATIONAL, wd.SRATIONAL):
        a, b = struct.unpack('<ii' if typ == wd.SRATIONAL else '<II', v)
        return f'{a}/{b}'
    return str(int.from_bytes(v, 'little', signed=typ == wd.SLONG))


def check(path):
    dng = open(path, 'rb').read()
    jpg = os.path.splitext(path)[0] + '.JPG'
    name = os.path.basename(path)
    if dng[:4] != b'II*\0':
        return print(f'{name}: not a DNG (no TIFF header)')
    want = wd.tiff_maker(wd.jpg_tiff(jpg)) if os.path.exists(jpg) else {}
    got = wd.tiff_maker(dng)
    if not got:
        return print(f'{name}: no MakerNote (not a --meta build?)')
    wb = neutral(dng)
    jwb = [struct.unpack('<II', want[t][1]) for t in (0x322, 0x323, 0x324)] if want else None
    print(f'{name}: AsShotNeutral ' + ' '.join(f'{a}/{b}' for a, b in wb) +
          (' (daylight default)' if wb == wd.DAYLIGHT_NEUTRAL else '') +
          ('' if jwb is None else ' == JPG' if wb == jwb else ' != JPG ' + ' '.join(f'{a}/{b}' for a, b in jwb)))
    if not want:
        return print('  no .JPG next to it: MakerNote not compared')
    bad = [t for t in sorted(want) if got.get(t) != want[t]]
    print(f'  MakerNote: {len(want) - len(bad)}/{len(want)} tags == JPG' +
          ('' if not bad else '; differ: ' + ', '.join(
              f'0x{t:X} {fmt(*got[t]) if t in got else "-"} (JPG {fmt(*want[t])})' for t in bad)))
    if bad or wb != jwb:                # where are the JPG's values in the job at hook time?
        dump = dng[wd.BASE + DUMP_AT:wd.BASE + DUMP_AT + DUMP_LEN]
        for t in dict.fromkeys(bad + ([0x322, 0x323, 0x324] if wb != jwb else [])):
            v = want[t][1]
            hits = [hex(i) for i in range(len(dump) - len(v) + 1) if dump[i:i + len(v)] == v]
            print(f'    0x{t:X} {fmt(*want[t])}: in job dump at ' + (', '.join(f'job+{h}' for h in hits) or 'nowhere'))


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    for p in sys.argv[1:]:
        check(p)


if __name__ == '__main__':
    main()
