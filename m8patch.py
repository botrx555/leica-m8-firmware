#!/usr/bin/env python3
"""
Build a patched Leica M8 firmware file from your own copy of Leica's stock update file, and
make picture-profile files for it.

  Option 1  raw-menu   "JPG fine+RAW" (14-bit RAW + JPG) listed in SET -> Compression and kept
                       after power-off.
  Option 2  dng        Option 1, plus the camera writes a 14-bit DNG in JPG fine+RAW mode (the
                       file keeps the .RAW name; Lightroom opens it).
  --info               add-on for either option (experimental): INFO in shooting mode shows a
                       Camera stats screen (shots taken, battery, temperature, lens code).
  --profiles           add-on for either option (experimental), instead of --info: Camera stats
                       plus a picture-profile picker: UP lists the profiles in the card's
                       \\PROFILES folder, SET loads one as the JPG tone curve until power-off.
                       The camera's own curves stay the default.
  --dial-ev            add-on for either option, alone or with --info / --profiles: the rear
                       dial sets exposure compensation in shooting mode (1/3 EV per click,
                       shown on the screen; closes after 2 s or with SET).
  --meta               add-on for dng, with any of the above: each DNG also carries the camera's
                       per-shot white balance (Lightroom's "As Shot" matches the JPG, auto or
                       preset) and the Leica MakerNote (body serial, lens code, light meter).
Both need Leica's stock firmware 2.014 (the only version tested); other files are refused.

Usage:  python3 m8patch.py raw-menu|dng path/to/m8-2_014.upd [--meta] [--info | --profiles]
                                                            [--dial-ev] [--out-dir DIR]
        python3 m8patch.py profiles path/to/m8-2_014.upd PROFILE... [--base srgb|modern|leica]
                                                                    [--out-dir DIR]

Firmware: your stock file is only read, never changed. The result is written to DIR (default:
build/ next to this script) as m8-<version>-<option>[-meta][-info|-profiles][-dial].upd. An existing
file is never overwritten, and only one of the known results below is written (TESTED): every
combination is built from pieces each tested on a camera, and checked as a whole in the emulator
(tools/m8_combotest.py); the status line says whether that exact file was also flashed.
Then copy it to an SD card and rename it there, as the script explains (README "Flash it").

Profiles: each PROFILE is a Nikon Picture Control file (.ncp), a text file of "input,output"
curve points, a folder of those, or a built-in curve (modern, moderndr). Writes DIR/PROFILES/
with one NAME.LUT per profile (NAME = the file name: 1-8 letters/digits) plus LEICA.LUT (the
camera's own curves, to switch back). Copy that PROFILES folder to the root of the card. The
.LUT files contain Leica data from your stock file: keep them private. Details: README
"Picture profiles".

Needs only Python 3 (standard library).
"""
import hashlib, os, sys

VERSION = '0.7.0'                   # project version (CHANGELOG.md), not a Leica firmware version
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'tools'))
from m8_rawpatch import KNOWN_INPUTS
import m8_dngpatch

# Status texts: the exact file was flashed and used on a camera, or only its pieces were.
EXACT = 'this exact build tested on a camera ({})'
PIECES = 'each piece tested on a camera; this combination tested in the emulator'
# (option, firmware, screen add-on, dial, meta) -> (SHA-256 of the result, status). Only these exact
# results are written. Pieces: Option 1 (RAW patch), DNG writer, INFO Camera stats, profile picker
# (includes Camera stats), dial EV, DNG writer with white balance + MakerNote -- see README "Builds".
TESTED = {
    ('raw-menu', '2.014', None, False, False): ('f6589c41a583d1a44fbce77c4acda707640d255acec97a0d2e1ac829c2e70396',
                                         EXACT.format('2026-10-19')),
    ('dng', '2.014', None, False, False): ('55264b11f86dd66e61f1412c5c341afdaa8553ce0d10ade270e52de1f24c944c',
                                    EXACT.format('2026-10-21')),
    ('dng', '2.014', 'info', False, False): ('32e6854ec0bc451667ef4f8f6b40dfb4c8af406a43db84e94b90abeb380408f8',
                                      EXACT.format('2026-10-23')),
    ('raw-menu', '2.014', 'info', False, False): ('fa4ae63a22e2df9749e989ccc570808ac24dabe1a3992593c4c37d27ad6c1c43',
                                           EXACT.format('2026-10-23')),
    ('dng', '2.014', 'profiles', False, False): ('d7a17ef25309af7780f24bc2bf240bfd8e68cb6e4edb6aa0845e07524d273b14',
                                          EXACT.format('2026-10-27')),
    ('raw-menu', '2.014', 'profiles', False, False): ('e170f4493f8562cf7ce67bdb286b27bdc9a089898f12b48b1916af99a4fb8f9d',
                                               EXACT.format('2026-10-27')),
    ('dng', '2.014', 'profiles', True, False): ('1254b7430a492c9197f2c4bb812bdd9a11e7d3b6a97fb043f245274b4fa07dfa',
                                         EXACT.format('2026-11-02')),
    ('raw-menu', '2.014', 'profiles', True, False): ('0776bad421170ca9a2dae0ebbbd956282e1821bd8ef7e587cabad43053d25b9d',
                                              PIECES),
    ('dng', '2.014', 'info', True, False): ('02301208cf12d4807936c466ae8a14c3c2fbd42c1010750038f7e759bd58c4b5', PIECES),
    ('raw-menu', '2.014', 'info', True, False): ('2181425138fec847e607d777afaae493df68a35658e8aac55934c407d46947c1',
                                          PIECES),
    ('dng', '2.014', None, True, False): ('31c1ac63b7aa332dcad0c430f3f575a3b8bf717327f156ad8ca135fb9ec2b074',
                                   EXACT.format('2026-11-04, on a second M8')),
    ('raw-menu', '2.014', None, True, False): ('823ef5c8f64311bacec4c27edd3e011f6afb065b564edb41f5ce5926ef24cb25',
                                        PIECES),
    ('dng', '2.014', None, False, True): ('9b9a489a0decff5bc1a52ba8ff2b4574a608ae7a8fa1bc41fc6bfa9a49c02c04', PIECES),
    ('dng', '2.014', None, True, True): ('bd914f4db75ab311f852acae81088e8250fe6ed90a093c89567d96e20708122a', PIECES),
    ('dng', '2.014', 'info', False, True): ('624448c6190de500028e4c583993cf9047c791504e5f9bc4c45a31b79af42af8', PIECES),
    ('dng', '2.014', 'info', True, True): ('4382b20c1e36e8c1b0fba0a4871e4391154b789f584f23785924690adc86bbe7', PIECES),
    ('dng', '2.014', 'profiles', False, True): ('a0161ed4991003134afd8a6dbcebbe2529dfddec3b3af02f46c2cc3c6e9c754f',
                                                PIECES),
    ('dng', '2.014', 'profiles', True, True): ('4359b04b41dd81761730e79fde3b46a09044e908c55cdc56cd8d8add3288e905',
                                               EXACT.format('2026-11-05')),
}
# Built, waiting for a camera test of a new piece: refused until then.
PENDING = {}
ADDON_TEXT = {'info': ' + INFO Camera stats (experimental)',
              'profiles': ' + Camera stats and picture-profile picker (experimental)'}
DIAL_TEXT = ' + exposure compensation on the rear dial'
META_TEXT = ' + per-shot white balance and Leica MakerNote in the DNG'
CARD_NAME = {'2.014': 'M8-2_014.UPD'}
OPTIONS = {'raw-menu': 'Option 1: JPG fine+RAW in the menu, kept after power-off',
           'dng': 'Option 2: Option 1 + 14-bit DNG written by the camera'}
MAX_PROFILES = 10                   # the camera lists the first 10 (directory order)


def fail(msg):
    raise SystemExit('error: ' + msg)


def stock_version(stock):
    version = KNOWN_INPUTS.get(hashlib.sha256(stock).hexdigest())
    if not version:
        fail('this is not Leica\'s stock M8 2.014 update file (SHA-256 does not match).\n'
             '       Only 2.014 is supported. Use the unmodified file; see README "Requirements".')
    return version


def build(option, stock, addon=None, dial=False, meta=False):
    version = stock_version(stock)
    key = (option, version, addon, dial, meta)
    if key in PENDING:
        fail('this combination is built but not yet tested on a camera; nothing was written.')
    _, out = m8_dngpatch.build_file(stock, option, addon, dial, meta)   # stock -> RAW patch -> pieces; region-checked
    want, status = TESTED[key]
    if hashlib.sha256(out).hexdigest() != want:
        fail('the result does not match the tested build; nothing was written. Please report this.')
    return version, out, status


def make_profiles(stock_path, sources, base, out_dir):
    """PROFILE arguments -> out_dir/PROFILES/NAME.LUT (+ LEICA.LUT). Returns [(name, info)]."""
    import m8_profile as mp, ncp
    stock_version(open(stock_path, 'rb').read())
    g = mp.stock_gamma(stock_path)
    st = mp.curves_of(g)
    items = []                                      # (name, path or built-in)
    for src in sources:
        if src.lower() in ('modern', 'moderndr'):
            items.append((src.upper(), src.lower()))
        elif os.path.isdir(src):
            found = sorted(f for f in os.listdir(src) if f.lower().endswith(('.ncp', '.txt')))
            if not found:
                fail(f'{src}: no .ncp or .txt files in this folder')
            items += [(os.path.splitext(f)[0], os.path.join(src, f)) for f in found]
        elif os.path.isfile(src):
            items.append((os.path.splitext(os.path.basename(src))[0], src))
        else:
            fail(f'{src}: no such file, folder or built-in curve (modern, moderndr)')
    names = {}
    for stem, src in items:
        name = ''.join(ch for ch in stem if ch.isascii() and ch.isalnum()).upper()
        if not name or len(name) > 8:
            fail(f'{src}: the camera needs a name of 1-8 letters/digits; rename the file (now "{stem}")')
        if name == 'LEICA':
            fail(f'{src}: LEICA is reserved for the camera\'s own curves; rename the file')
        if name in names:
            fail(f'{src} and {names[name]} both become {name}.LUT; rename one of them')
        names[name] = src
    if len(names) + 1 > MAX_PROFILES:
        fail(f'{len(names)} profiles + LEICA: the camera lists at most {MAX_PROFILES}; leave some out')
    tables = [('LEICA', g, 'the camera\'s own curves (exact copy)')]
    for name, src in names.items():
        if src in ('modern', 'moderndr'):
            tone, info = mp.BUILTIN[src](st), 'built-in curve'
        else:
            raw = open(src, 'rb').read()
            if raw[:4] == b'NCP\0':
                try:
                    p = ncp.read(raw)
                except ValueError as e:
                    fail(f'{src}: {e}')
                curve, info = p['curve'], f'Nikon "{p["name"]}", {ncp.describe(p)}'
            else:
                try:
                    curve, info = mp.points_curve(raw.decode('utf-8')), 'curve points'
                except (UnicodeDecodeError, ValueError):
                    fail(f'{src}: neither a Nikon .ncp file nor "input,output" curve points')
            tone = mp.on_base(curve, mp.BUILTIN[base](st))
            info += f'; on {base} base'
        tables.append((name, mp.build(g, tone), info))
    dst = os.path.join(out_dir, 'PROFILES')
    os.makedirs(dst, exist_ok=True)
    for name, data, _ in tables:                    # check everything before writing anything
        path = os.path.join(dst, name + '.LUT')
        if os.path.exists(path) and open(path, 'rb').read() != data:
            fail(f'{path} already exists with other contents; not overwriting it. Move or delete it first.')
    for name, data, _ in tables:
        path = os.path.join(dst, name + '.LUT')
        if not os.path.exists(path):
            with open(path, 'xb') as f:
                f.write(data)
    return dst, [(n, i) for n, _, i in tables]


def main():
    args = sys.argv[1:]
    out_dir = os.path.join(HERE, 'build')
    base = 'srgb'
    for opt in ('--out-dir', '--base'):
        if opt in args:
            i = args.index(opt)
            if i + 1 >= len(args):
                fail(f'{opt} needs a value')
            if opt == '--out-dir':
                out_dir = args[i + 1]
            else:
                base = args[i + 1]
            del args[i:i + 2]
    if args in (['--version'], ['-V']):
        raise SystemExit(f'm8patch v{VERSION}')

    if args and args[0] == 'profiles':
        if len(args) < 3:
            raise SystemExit(f'm8patch v{VERSION}\n' + __doc__)
        if base not in ('srgb', 'modern', 'leica'):
            fail('--base must be srgb, modern or leica')
        if not os.path.isfile(args[1]):
            fail(f'{args[1]}: no such file')
        dst, made = make_profiles(args[1], args[2:], base, out_dir)
        print(f'm8patch v{VERSION} -- picture profiles\n  folder:  {dst}')
        for name, info in made:
            print(f'  {name + ".LUT":13s} {info}')
        print(f"""
Next (needs a build made with --profiles):
  1. Copy the PROFILES folder to the root of the camera's SD card.
  2. In shooting mode: INFO -> UP -> choose with UP/DOWN -> SET loads it ("Loaded").
     The next shots use it until power-off; choose LEICA (or switch off and on) for the camera's own curves.
  The .LUT files contain Leica data from your stock file: keep them private.""")
        return

    dial, meta = '--dial-ev' in args, '--meta' in args
    args = [a for a in args if a not in ('--dial-ev', '--meta')]
    addon = None
    for flag in ('--info', '--profiles'):
        if flag in args:
            if addon:
                fail('use either --info or --profiles (--profiles includes Camera stats)')
            addon = flag[2:]
            args.remove(flag)
    if len(args) != 2 or args[0] not in OPTIONS:
        raise SystemExit(f'm8patch v{VERSION}\n' + __doc__)
    option, src = args
    if meta and option != 'dng':
        fail('--meta is an add-on for the dng option (it adds to the DNG the camera writes)')
    if not os.path.isfile(src):
        fail(f'{src}: no such file')
    version, out, status = build(option, open(src, 'rb').read(), addon, dial, meta)

    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, f'm8-{version.replace(".", "_")}-{option}{"-meta" if meta else ""}'
                                f'{"-" + addon if addon else ""}{"-dial" if dial else ""}.upd')
    if os.path.exists(dst):
        fail(f'{dst} already exists; not overwriting it. Move or delete it first.')
    with open(dst, 'xb') as f:                      # 'x' = create only, never replace a file
        f.write(out)
    sha = hashlib.sha256(out).hexdigest()
    card = CARD_NAME[version]
    after = {None: '',
             'info': '\n  6. INFO in shooting mode shows Camera stats; INFO or SET closes it.',
             'profiles': '\n  6. INFO in shooting mode shows Camera stats; UP opens the profile list (make the'
                         '\n     card folder with: python3 m8patch.py profiles <stock file> <your .ncp files>).'}[addon]
    step = 6 + bool(addon)
    if dial:
        after += (f'\n  {step}. In shooting mode, turn the rear dial: exposure compensation changes by 1/3 EV'
                  '\n     per click and shows on the screen; it closes after 2 s or with SET. The value stays set.')
        step += 1
    if meta:
        after += (f'\n  {step}. In Lightroom, White Balance "As Shot" is now the camera\'s own (as in the JPG,'
                  '\n     auto or preset), and the Leica MakerNote (body serial, lens code) is in the file.')
    print(f"""m8patch v{VERSION} -- {OPTIONS[option]}{META_TEXT if meta else ''}{ADDON_TEXT.get(addon, '')}{DIAL_TEXT if dial else ''}
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
  5. After the restart: SET -> Compression -> JPG fine+RAW.""" + after)


if __name__ == '__main__':
    main()
