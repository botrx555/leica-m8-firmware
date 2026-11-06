# Changelog

Versions of this project (not Leica firmware versions). Every build is for Leica M8 firmware **2.014**.
Hashes are the SHA-256 of the file `m8patch.py` writes; if yours differs, don't flash it.

## v0.8.0 — 2026-11-06

- **Simpler: one build, two add-ons.** The persistent RAW menu, the in-camera 14-bit DNG and the
  camera's white balance + Leica MakerNote (v0.7.0's `dng --meta`) are now one build:
  `python3 m8patch.py m8-2_014.upd [--info] [--dial-ev]`. 4 builds instead of 18; the output is
  `build/m8-2_014-patched[-info][-dial].upd`. The files open in Lightroom as before, and m8raw2dng2
  still converts them.
- No longer offered: Option 1 alone (`raw-menu`), the DNG without white balance (`dng` without
  `--meta`), and the picture-profile picker (`--profiles`, and `m8patch.py profiles`). The picker
  is a work in progress (README); its tools stay in `tools/`. Earlier hashes stay below.
- The four builds are byte-identical to v0.7.0's `dng --meta [--info] [--dial-ev]` (same hashes).
  `--info --dial-ev` (`4382b20c…`) was flashed and camera-tested as that exact file on 2026-11-06.
- Developers: `m8_dngpatch.BUILDS` = the 4 offered builds, `ALL_BUILDS` = every earlier one;
  combotest, evtest and savetest test the 4 by default (`--build` still takes any).

| Build | SHA-256 | On a camera |
|---|---|---|
| the build | `9b9a489a0decff5bc1a52ba8ff2b4574a608ae7a8fa1bc41fc6bfa9a49c02c04` | pieces |
| + `--info` | `624448c6190de500028e4c583993cf9047c791504e5f9bc4c45a31b79af42af8` | pieces |
| + `--dial-ev` | `bd914f4db75ab311f852acae81088e8250fe6ed90a093c89567d96e20708122a` | pieces |
| + `--info --dial-ev` | `4382b20c1e36e8c1b0fba0a4871e4391154b789f584f23785924690adc86bbe7` | exact file (2026-11-06) |

## v0.7.0 — 2026-11-06

- `dng --dial-ev` (`31c1ac63…`) has now been flashed and tested as that exact file, and `dng`
  (`55264b11…`) tested again, on a second M8 by vcs222 (2026-11-04, PR #6). `m8patch.py` and the
  README "Builds" table say so.
- Developers: builds are made from pieces with the same flags as `m8patch.py`
  (`tools/m8_dngpatch.py … dng --profiles --dial-ev`), and each piece's camera test is recorded
  (`TESTED_PIECES`). The numbered "stages" are kept only as history: the diagnostic builds under
  `--stage N`, the old numbers of regular builds as aliases. `m8_savetest.py --build …` tests one
  build; by default it tests all 12 plus the diagnostic builds. Every build's bytes and hash are
  unchanged.
- **New add-on, `--meta`, for Option 2 (`dng`), with any other add-on:** each DNG also carries
  the camera's per-shot white balance, so Lightroom's "As Shot" matches the JPG (auto or preset),
  and the JPG's Leica MakerNote (body serial, lens code, meter readings). Before, every file opened
  at the same daylight balance. In JPG fine+RAW mode the camera now asks the image processor for the
  white balance before saving the RAW, the order stock firmware uses for DNG+JPG mode. 6 new
  builds. `dng --meta --profiles --dial-ev` (`4359b04b…`) was camera-tested as that exact file on
  2026-11-05/06: auto WB, Tungsten preset and a long exposure with a .BIA dark frame.
- Developers: `tools/asm/m8_dngmeta.s`, `tools/m8_metacheck.py` (checks camera files against their
  JPGs), `m8_wrapdng.py --meta`; combotest, evtest and savetest cover the `--meta` builds.
- Builds without `--meta` are unchanged (same hashes as v0.6.0).

| Build | SHA-256 | On a camera |
|---|---|---|
| 2 `dng --meta` | `9b9a489a0decff5bc1a52ba8ff2b4574a608ae7a8fa1bc41fc6bfa9a49c02c04` | pieces |
| 2 `dng --meta --info` | `624448c6190de500028e4c583993cf9047c791504e5f9bc4c45a31b79af42af8` | pieces |
| 2 `dng --meta --profiles` | `a0161ed4991003134afd8a6dbcebbe2529dfddec3b3af02f46c2cc3c6e9c754f` | pieces |
| 2 `dng --meta --dial-ev` | `bd914f4db75ab311f852acae81088e8250fe6ed90a093c89567d96e20708122a` | pieces |
| 2 `dng --meta --info --dial-ev` | `4382b20c1e36e8c1b0fba0a4871e4391154b789f584f23785924690adc86bbe7` | pieces |
| 2 `dng --meta --profiles --dial-ev` | `4359b04b41dd81761730e79fde3b46a09044e908c55cdc56cd8d8add3288e905` | exact file (2026-11-05) |

## v0.6.0 — 2026-11-02

- **New add-on, `--dial-ev`, for either option:** in shooting mode the rear dial sets exposure
  compensation, ⅓ EV per click (−3 to +3 EV), shown on the screen; the screen closes 2 s after the
  last click or with SET (or any other button). The value is the SET menu's own setting, changed by
  Leica's unused dial routine found in the firmware; it stays set after power-off and shows in the
  shot's EXIF. Picture in the README (`docs/assets/dial-ev.png`).
- **Add-ons combine:** `--info` or `--profiles`, plus `--dial-ev`, with either option: 12 builds.
  Each is made of pieces that were all tested on a camera, kept byte-identical in every combination
  (checked by the new `tools/m8_combotest.py`); the README "Builds" table and `m8patch.py` say which
  exact files were also flashed.
- Developers: `tools/asm/m8_infoev.s`, `tools/m8_evtest.py`; stages 15–20 in `m8_dngpatch.py`.
- Builds without `--dial-ev` are unchanged (same hashes as v0.5.0).

| Build | SHA-256 | On a camera |
|---|---|---|
| 1 `raw-menu` | `f6589c41a583d1a44fbce77c4acda707640d255acec97a0d2e1ac829c2e70396` | exact file (2026-10-19) |
| 1 `raw-menu --info` | `fa4ae63a22e2df9749e989ccc570808ac24dabe1a3992593c4c37d27ad6c1c43` | exact file (2026-10-23) |
| 1 `raw-menu --profiles` | `e170f4493f8562cf7ce67bdb286b27bdc9a089898f12b48b1916af99a4fb8f9d` | exact file (2026-10-27) |
| 1 `raw-menu --dial-ev` | `823ef5c8f64311bacec4c27edd3e011f6afb065b564edb41f5ce5926ef24cb25` | pieces |
| 1 `raw-menu --info --dial-ev` | `2181425138fec847e607d777afaae493df68a35658e8aac55934c407d46947c1` | pieces |
| 1 `raw-menu --profiles --dial-ev` | `0776bad421170ca9a2dae0ebbbd956282e1821bd8ef7e587cabad43053d25b9d` | pieces |
| 2 `dng` | `55264b11f86dd66e61f1412c5c341afdaa8553ce0d10ade270e52de1f24c944c` | exact file (2026-10-21) |
| 2 `dng --info` | `32e6854ec0bc451667ef4f8f6b40dfb4c8af406a43db84e94b90abeb380408f8` | exact file (2026-10-23) |
| 2 `dng --profiles` | `d7a17ef25309af7780f24bc2bf240bfd8e68cb6e4edb6aa0845e07524d273b14` | exact file (2026-10-27) |
| 2 `dng --dial-ev` | `31c1ac63b7aa332dcad0c430f3f575a3b8bf717327f156ad8ca135fb9ec2b074` | pieces |
| 2 `dng --info --dial-ev` | `02301208cf12d4807936c466ae8a14c3c2fbd42c1010750038f7e759bd58c4b5` | pieces |
| 2 `dng --profiles --dial-ev` | `1254b7430a492c9197f2c4bb812bdd9a11e7d3b6a97fb043f245274b4fa07dfa` | exact file (2026-11-02) |

## v0.5.0 — 2026-10-27

- **New add-on, `--profiles` (experimental), for either option:** Camera stats plus a
  picture-profile picker. INFO → UP lists the `.LUT` files in the card's `PROFILES` folder; SET
  loads one as the JPG tone curve until power-off. The camera's own curves stay the default.
  Both options with `--profiles` are camera-tested.
- **New command, `m8patch.py profiles`:** turns Nikon Picture Control files (`.ncp`), curve-point
  files or the built-in `modern` / `moderndr` curves into a card-ready `PROFILES` folder (always
  with `LEICA.LUT`). Profiles keep the Contrast setting working and look the same in every colour
  space.
- Developers: `tools/m8_profile.py` (curve engine), `tools/ncp.py`, `tools/m8_curvesim.py` (renders
  curves from your RAW files), `tools/m8_curvetest.py`; picker tests in `m8_infotest.py` /
  `m8_inforender.py`.
- Tried and rolled back: a build with the `MODERN` curve built in as the default (stage 12).
- README: Safety section states the three RAW-patch instructions and when card recovery works;
  where to get the stock 2.014 file; the 2.024 downgrade note.
- Options without an add-on, and with `--info`, are unchanged (same hashes as v0.4.0).

| Build | SHA-256 | Tested on a camera |
|---|---|---|
| 1 `raw-menu` | `f6589c41a583d1a44fbce77c4acda707640d255acec97a0d2e1ac829c2e70396` | yes (2026-10-19) |
| 2 `dng` | `55264b11f86dd66e61f1412c5c341afdaa8553ce0d10ade270e52de1f24c944c` | yes (2026-10-21) |
| 1 `raw-menu --info` | `fa4ae63a22e2df9749e989ccc570808ac24dabe1a3992593c4c37d27ad6c1c43` | yes (2026-10-23) |
| 2 `dng --info` | `32e6854ec0bc451667ef4f8f6b40dfb4c8af406a43db84e94b90abeb380408f8` | yes (2026-10-23) |
| 1 `raw-menu --profiles` | `e170f4493f8562cf7ce67bdb286b27bdc9a089898f12b48b1916af99a4fb8f9d` | yes (2026-10-27) |
| 2 `dng --profiles` | `d7a17ef25309af7780f24bc2bf240bfd8e68cb6e4edb6aa0845e07524d273b14` | yes (2026-10-27) |

## v0.4.0 — 2026-10-23

- **New add-on, `--info` (experimental), for either option:** in shooting mode the INFO button (which
  does nothing on a stock M8) shows a Camera stats screen: shots taken (the body's shutter counter),
  battery, temperature and lens code, the values of Leica's hidden service page "Body Debug Data".
  INFO or SET closes it. Service mode is unchanged. Picture in the README
  (`docs/assets/info-camera-stats.png`).
- Options 1 and 2 without `--info` are unchanged (same hashes as v0.3.0).
- Developers: emulator tests for the INFO builds (`m8_infotest.py`, every key in every GUI state;
  `m8_inforender.py`, renders the screen with the camera's own drawing code); README lists how long
  each test takes.

| Build | SHA-256 | Tested on a camera |
|---|---|---|
| 1 `raw-menu` | `f6589c41a583d1a44fbce77c4acda707640d255acec97a0d2e1ac829c2e70396` | yes (2026-10-19) |
| 2 `dng` | `55264b11f86dd66e61f1412c5c341afdaa8553ce0d10ade270e52de1f24c944c` | yes (2026-10-21) |
| 1 `raw-menu --info` | `fa4ae63a22e2df9749e989ccc570808ac24dabe1a3992593c4c37d27ad6c1c43` | yes (2026-10-23) |
| 2 `dng --info` | `32e6854ec0bc451667ef4f8f6b40dfb4c8af406a43db84e94b90abeb380408f8` | yes (2026-10-23) |

## v0.3.0 — 2026-10-23

- **One command for users:** `m8patch.py` builds either option from Leica's stock 2.014 file:
  - `raw-menu` (Option 1): "JPG fine+RAW" in SET → Compression, kept after power-off.
  - `dng` (Option 2): Option 1 plus a 14-bit DNG written by the camera.
  It only reads the stock file, never overwrites anything, and only writes results identical to
  the camera-tested builds.
- **Option 2: black level now follows the ISO** (92 at ISO 160, doubling per step to 1472 at ISO
  2500). High-ISO files no longer open lifted / washed out in Lightroom. Camera-tested at ISO 160,
  320, 640, 1250 and 2500.
- Firmware 2.024 is no longer offered (it was never tested on a camera).
- README: "Why 14-bit?", options table, step-by-step build and flash.

| Option | SHA-256 | Tested on a camera |
|---|---|---|
| 1 `raw-menu` | `f6589c41a583d1a44fbce77c4acda707640d255acec97a0d2e1ac829c2e70396` | yes (2026-10-19) |
| 2 `dng` | `55264b11f86dd66e61f1412c5c341afdaa8553ce0d10ade270e52de1f24c944c` | yes (2026-10-21) |

## v0.2.0 — 2026-10-20

- First in-camera 14-bit DNG (`tools/m8_dngpatch.py --stage 4`): JPG fine+RAW writes a JPG plus a
  `.RAW` file whose contents are a 14-bit DNG that Lightroom opens, with exposure, ISO and date.
  Build `59aff4a35f5c8fea4ca1fc8ab962ac3e0ac171131bd82410cf67c9231665273c`, camera-tested.
  BlackLevel was 92 at every ISO (fixed in v0.3.0).
- `.DNG` file names (stage 5) were tried and rolled back: in-camera review could not show the JPG.

## v0.1.0 — 2026-10-19

- Persistent "JPG fine+RAW" (`tools/m8_rawpatch.py`): listed in the SET menu without the button
  sequence and kept after power-off. Build
  `f6589c41a583d1a44fbce77c4acda707640d255acec97a0d2e1ac829c2e70396`, camera-tested.
