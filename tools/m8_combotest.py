#!/usr/bin/env python3
"""
Check every m8patch.py combination (base x screen add-on x dial = 12 builds) against the rule
"each piece tested on a camera" (README "Builds"; owner's decision 2026-11-02).

Each build is made through m8patch.build() -- the path a user runs -- and must:
  - equal its m8_dngpatch stage file and the SHA-256 in m8patch.TESTED;
  - differ from Option 1 (the RAW patch, camera-tested) only inside the regions of its pieces;
  - carry each piece byte-identical to the camera-tested build that introduced it:
      dng       header, ldimg, hook                -> stage 4  (55264b11, 2026-10-21)
      info      memtest12, infokey                 -> stage 8  (32e6854e, 2026-10-23)
      profiles  bfmem, infokey                     -> stage 13 (d7a17ef2, 2026-10-27)
      dial      setting, evkey, evheld, evtimer    -> stage 15 (1254b743, 2026-11-02)
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

PIECES = {'dng': (4, ['header', 'ldimg', 'hook']),
          'info': (8, ['memtest12', 'infokey']),
          'profiles': (13, ['bfmem', 'infokey']),
          'dial': (15, ['setting', 'evkey', 'evheld', 'evtimer'])}


def region_bytes(upd, name):
    pos, _ = d.find_lump(upd, b'PXA')
    lo, hi = d.REGIONS[name]
    return upd[pos + lo - d.PXA_BASE:pos + hi - d.PXA_BASE]


def main():
    res = Results('-v' in sys.argv)
    stock = open(os.path.join(ROOT, 'firmware/stock/m8-2_014.upd'), 'rb').read()
    option1 = d.flashed_file(stock)
    refs = {p: d.stage_file(stock, n)[1] for p, (n, _) in PIECES.items()}
    for p, (n, _) in PIECES.items():
        res.check(f'reference for {p}: stage {n} is camera-tested', 'flashed and camera-tested' in
                  d.KNOWN_OUTPUTS.get(hashlib.sha256(refs[p]).hexdigest(), ''), True)
    res.check('12 combinations', len(m8patch.STAGE), 12)
    for (option, screen, dial), stage in sorted(m8patch.STAGE.items(), key=str):
        name = f'{option}{" --" + screen if screen else ""}{" --dial-ev" if dial else ""}'
        _, out, status = m8patch.build(option, stock, screen, dial)
        want = m8patch.TESTED[option, '2.014', screen, dial][0]
        res.check(f'{name}: SHA-256 as in TESTED', hashlib.sha256(out).hexdigest(), want)
        if stage is not None:
            res.check(f'{name}: == m8_dngpatch stage {stage}', out == d.stage_file(stock, stage)[1], True)
        pieces = (['dng'] if option == 'dng' else []) + ([screen] if screen else []) + (['dial'] if dial else [])
        regions = [r for p in pieces for r in PIECES[p][1]]
        res.check(f'{name}: pieces\' regions disjoint', len(regions), len(set(regions)))
        spans = [d.REGIONS[r] for r in regions]
        res.check(f'{name}: no two regions overlap', [(a, b) for a in spans for b in spans
                                                      if a < b and a[1] > b[0]], [])
        pos, size = d.find_lump(out, b'PXA')
        res.check(f'{name}: differs from Option 1 only in its pieces\' regions',
                  d.region_violations(option1, out, pos, size, set(regions)), [])
        for p in pieces:
            same = [r for r in PIECES[p][1] if region_bytes(out, r) == region_bytes(refs[p], r)]
            res.check(f'{name}: piece {p} byte-identical to stage {PIECES[p][0]}', same, PIECES[p][1])
        exact = status.startswith('this exact build tested')
        known = d.KNOWN_OUTPUTS.get(want, '') if stage is not None else 'flashed and camera-tested'
        res.check(f'{name}: status matches the record ({"exact" if exact else "pieces"})',
                  exact, 'flashed and camera-tested' in known or stage is None)
    print(f'{res.ok} passed, {res.bad} failed')
    sys.exit(1 if res.bad else 0)


if __name__ == '__main__':
    main()
