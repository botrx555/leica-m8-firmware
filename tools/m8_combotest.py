#!/usr/bin/env python3
"""
Check every m8patch.py build (the base dng --meta, with --info and/or --dial-ev = 4 builds) against
the rule
"each piece tested on a camera" (README "Builds"; owner's decision 2026-11-02).

Each build is made through m8patch.build() -- the path a user runs -- and must:
  - equal m8_dngpatch.build_file() for its flags and the SHA-256 in m8patch.TESTED;
  - differ from Option 1 (the RAW patch, camera-tested) only inside the regions of its pieces;
  - carry each piece byte-identical to the camera-tested build that introduced it
    (m8_dngpatch.TESTED_PIECES: dng 55264b11, info 32e6854e, profiles d7a17ef2, dial 1254b743,
    dial iso 796ecbc6, meta 4359b04b);
  - keep the pieces' regions disjoint (info and profiles share infokey and overlap: never together).
Behaviour of the combined code is tested by m8_infotest / m8_inforender / m8_evtest / m8_savetest.

Usage:  work/venv/bin/python tools/m8_combotest.py [-v]
"""
import hashlib, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import m8_dngpatch as d
import m8patch
from m8_emutest import Results



def region_bytes(upd, name):
    pos, _ = d.find_lump(upd, b'PXA')
    lo, hi = d.REGIONS[name]
    return upd[pos + lo - d.PXA_BASE:pos + hi - d.PXA_BASE]


def main():
    res = Results('-v' in sys.argv)
    stock = open(os.path.join(ROOT, 'firmware/stock/m8-2_014.upd'), 'rb').read()
    option1 = d.flashed_file(stock)
    refs, PIECES = {}, {}
    known = {hashlib.sha256(d.build_file(stock, *b)[1]).hexdigest(): b for b in d.ALL_BUILDS}
    for p, (names, sha, date) in d.TESTED_PIECES.items():
        if not names:
            continue
        refs[p] = d.build_file(stock, *known[sha])[1]      # the flashed build that introduced it
        PIECES[p] = sorted(d.regions_of(names))
        res.check(f'reference for {p}: {sha[:8]} is a camera-tested build ({date})', 'flashed and camera-tested'
                  in d.KNOWN_OUTPUTS.get(sha, ''), True)
        res.check(f'reference for {p}: contains the piece', set(names) <= set(d.build_pieces(*known[sha])), True)
    res.check('4 builds, the same in m8patch.TESTED', (len(d.BUILDS), sorted(d.BUILDS, key=str) == sorted(
        [('dng', sc, dl, True) for (_, sc, dl) in m8patch.TESTED], key=str)), (4, True))
    for option, screen, dial, meta in d.BUILDS:
        name = d.build_name(option, screen, dial, meta)
        _, out, status = m8patch.build(stock, screen, dial)
        want = m8patch.TESTED['2.014', screen, dial][0]
        res.check(f'{name}: SHA-256 as in TESTED', hashlib.sha256(out).hexdigest(), want)
        res.check(f'{name}: == m8_dngpatch.build_file', out == d.build_file(stock, option, screen, dial, meta)[1],
                  True)
        pieces = (['meta'] if meta else ['dng'] if option == 'dng' else []) + ([screen] if screen else []) + \
            (['dial iso'] if dial == 'iso' else ['dial'] if dial else [])
        regions = [r for p in pieces for r in PIECES[p]]
        res.check(f'{name}: pieces\' regions disjoint', len(regions), len(set(regions)))
        spans = [d.REGIONS[r] for r in regions]
        res.check(f'{name}: no two regions overlap', [(a, b) for a in spans for b in spans
                                                      if a < b and a[1] > b[0]], [])
        pos, size = d.find_lump(out, b'PXA')
        res.check(f'{name}: differs from Option 1 only in its pieces\' regions',
                  d.region_violations(option1, out, pos, size, set(regions)), [])
        for p in pieces:
            same = [r for r in PIECES[p] if region_bytes(out, r) == region_bytes(refs[p], r)]
            res.check(f'{name}: piece {p} byte-identical to its camera-tested build '
                      f'{d.TESTED_PIECES[p][1][:8]}', same, PIECES[p])
        exact = status.startswith('this exact build tested')
        res.check(f'{name}: status matches the record ({"exact" if exact else "pieces"})',
                  exact, 'flashed and camera-tested' in d.KNOWN_OUTPUTS.get(want, ''))
    print(f'{res.ok} passed, {res.bad} failed')
    sys.exit(1 if res.bad else 0)


if __name__ == '__main__':
    main()
