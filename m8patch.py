#!/usr/bin/env python3
"""
Build a patched Leica M8 firmware file from your own copy of Leica's stock update file.

  The build   "JPG fine+RAW" (14-bit) listed in SET -> Compression and kept after power-off; in
              that mode the camera writes a JPG plus a 14-bit DNG (the file keeps the .RAW name;
              Lightroom opens it) with the shot's exposure, ISO, date, the camera's own white
              balance (Lightroom's "As Shot" matches the JPG) and the Leica MakerNote.
  --info      add-on (experimental): INFO in shooting mode shows a Camera stats screen (shots
              taken, battery, temperature, lens code).
  --dial-ev   add-on: the rear dial sets exposure compensation in shooting mode (1/3 EV per
              click, shown on the screen; closes after 2 s or with SET).
Needs Leica's stock firmware 2.014 (the only version tested); other files are refused.

Usage:  python3 m8patch.py path/to/m8-2_014.upd [--info] [--dial-ev] [--out-dir DIR]

Your stock file is only read, never changed. The result is written to DIR (default: build/ next
to this script) as m8-<version>-patched[-info][-dial].upd. An existing file is never overwritten,
and only one of the known results below is written (TESTED): every build is made of pieces each
tested on a camera, and checked as a whole in the emulator (tools/m8_combotest.py); the status
line says whether that exact file was also flashed. Then copy it to an SD card and rename it
there, as the script explains (README "Flash it").

Earlier versions offered more combinations (Option 1 alone, the DNG without white balance, the
picture-profile picker); their hashes are in CHANGELOG.md. Needs only Python 3 (standard library).
"""
import hashlib, os, sys

VERSION = '0.8.0'                   # project version (CHANGELOG.md), not a Leica firmware version
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'tools'))
from m8_rawpatch import KNOWN_INPUTS
import m8_dngpatch

# Status texts: the exact file was flashed and used on a camera, or only its pieces were.
EXACT = 'this exact build tested on a camera ({})'
PIECES = 'each piece tested on a camera; this combination tested in the emulator'
# (firmware, INFO add-on, dial) -> (SHA-256 of the result, status). Only these exact results are
# written. Pieces: RAW patch + DNG writer with white balance and MakerNote (m8_dngpatch `dng --meta`),
# INFO Camera stats, dial EV -- see README "Builds".
TESTED = {
    ('2.014', None, False): ('9b9a489a0decff5bc1a52ba8ff2b4574a608ae7a8fa1bc41fc6bfa9a49c02c04', PIECES),
    ('2.014', 'info', False): ('624448c6190de500028e4c583993cf9047c791504e5f9bc4c45a31b79af42af8', PIECES),
    ('2.014', None, True): ('bd914f4db75ab311f852acae81088e8250fe6ed90a093c89567d96e20708122a', PIECES),
    ('2.014', 'info', True): ('4382b20c1e36e8c1b0fba0a4871e4391154b789f584f23785924690adc86bbe7',
                              EXACT.format('2026-11-06')),
}
# Built, waiting for a camera test of a new piece: refused until then.
PENDING = {}
BASE_TEXT = 'JPG fine+RAW in the menu + 14-bit DNG with the camera\'s white balance'
INFO_TEXT = ' + INFO Camera stats (experimental)'
DIAL_TEXT = ' + exposure compensation on the rear dial'
CARD_NAME = {'2.014': 'M8-2_014.UPD'}


def fail(msg):
    raise SystemExit('error: ' + msg)


def stock_version(stock):
    version = KNOWN_INPUTS.get(hashlib.sha256(stock).hexdigest())
    if not version:
        fail('this is not Leica\'s stock M8 2.014 update file (SHA-256 does not match).\n'
             '       Only 2.014 is supported. Use the unmodified file; see README "Requirements".')
    return version


def build(stock, info=None, dial=False):
    version = stock_version(stock)
    key = (version, info, dial)
    if key in PENDING:
        fail('this combination is built but not yet tested on a camera; nothing was written.')
    _, out = m8_dngpatch.build_file(stock, 'dng', info, dial, True)   # stock -> RAW patch -> pieces; region-checked
    want, status = TESTED[key]
    if hashlib.sha256(out).hexdigest() != want:
        fail('the result does not match the tested build; nothing was written. Please report this.')
    return version, out, status


def main():
    args = sys.argv[1:]
    out_dir = os.path.join(HERE, 'build')
    if '--out-dir' in args:
        i = args.index('--out-dir')
        if i + 1 >= len(args):
            fail('--out-dir needs a value')
        out_dir = args[i + 1]
        del args[i:i + 2]
    if args in (['--version'], ['-V']):
        raise SystemExit(f'm8patch v{VERSION}')
    if args and args[0] in ('raw-menu', 'dng', 'profiles'):
        fail(f'"{args[0]}" is gone since v0.8.0: there is one build now (README "Build your firmware file").\n'
             '       Run: python3 m8patch.py path/to/m8-2_014.upd [--info] [--dial-ev]')
    info = 'info' if '--info' in args else None
    dial = '--dial-ev' in args
    args = [a for a in args if a not in ('--info', '--dial-ev')]
    if len(args) != 1 or args[0].startswith('-'):
        raise SystemExit(f'm8patch v{VERSION}\n' + __doc__)
    src = args[0]
    if not os.path.isfile(src):
        fail(f'{src}: no such file')
    version, out, status = build(open(src, 'rb').read(), info, dial)

    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, f'm8-{version.replace(".", "_")}-patched'
                                f'{"-info" if info else ""}{"-dial" if dial else ""}.upd')
    if os.path.exists(dst):
        fail(f'{dst} already exists; not overwriting it. Move or delete it first.')
    with open(dst, 'xb') as f:                      # 'x' = create only, never replace a file
        f.write(out)
    sha = hashlib.sha256(out).hexdigest()
    card = CARD_NAME[version]
    step = 7
    after = ''
    if info:
        after += f'\n  {step}. INFO in shooting mode shows Camera stats; INFO or SET closes it.'
        step += 1
    if dial:
        after += (f'\n  {step}. In shooting mode, turn the rear dial: exposure compensation changes by 1/3 EV'
                  '\n     per click and shows on the screen; it closes after 2 s or with SET. The value stays set.')
    print(f"""m8patch v{VERSION} -- {BASE_TEXT}{INFO_TEXT if info else ''}{DIAL_TEXT if dial else ''}
  input:   {src}  (stock {version}, unchanged)
  output:  {dst}
  SHA-256: {sha}
  status:  matches the author's build: {status}

Next (README "Flash it" and "Safety and recovery"):
  1. Charge the battery fully. Keep a separate card holding only your stock {version} file.
  2. Format an SD card in the camera, then copy the output file onto it.
  3. On the card, rename it to exactly {card}  (the camera only looks for M8.UPD / M8-x_yyy.UPD).
     Rename the copy on the card, not your stock file.
  4. Run the camera's normal firmware update; don't touch the battery or card until it finishes.
  5. After the restart: SET -> Compression -> JPG fine+RAW.
  6. Open a test shot's .RAW in Lightroom: White Balance "As Shot" should look like the JPG.""" + after)


if __name__ == '__main__':
    main()
