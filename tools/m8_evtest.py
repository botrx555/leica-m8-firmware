#!/usr/bin/env python3
"""
Emulator test of exposure compensation on the rear dial (tools/asm/m8_infoev.s): every m8_dngpatch
build with --dial-ev (pieces ev, ev key, ev held, ev timer) against the same build without it.

  1. Key path: the camera's real application key handler 0x44E30 (+ service gate 0x403F0 + the
     INFO code + ev_key) on each EV build and on its base build, every key code in every GUI state
     below; every call out (with arguments) and every write outside the stack is recorded. Expected:
     identical except the dial keys c / w in the normal state, where the EV build additionally makes
     Leica's dial step 0x65954(-, key), opens camInfoMenu + BodyDebugDataDialog exactly as the INFO
     path does, points the dialog at vt_ev and arms the dialog's timer (0x8BD10(dialog+0x60, 0x271E,
     2000, 1, 0, 0, 0)) -- nothing else; the same with `a` held (app+0x2548C, where stock ignores
     every key). Service mode, Simple mode (override profile 5), shutter fully pressed (app+0x2548D),
     Leica's own dial window enabled by the body (app+0x25744 bit 0), app+0x254B0, app+0x254A8 and
     [0x169CC8] states: identical for all keys; the handler's return value is unchanged throughout.
  2. on_key (page open), every key: c / w -> step, re-arm, redraw; any other key -> the dialog's
     own method table back and its own SET path (0x61868(dialog, 's')); no other writes.
  3. ev_timer (GUI event 0x271E): our page open -> closed as by SET; our page already hidden ->
     only the method table restored; any other method table (Leica's, the INFO page's) -> Leica's
     handler 0x43B64 with the same arguments and no writes.
  4. Leica's dial step 0x65954 itself (real code; message calls stubbed): every start value 0..18,
     both keys: new value, clamping at 0 / 18, working profile 4 (as the SET menu), event 0x2715.
  5. Paint: the real dialog base paint 0x605AC with our title / content and the real GUI drawing
     code and fonts (m8_inforender.Screen); PNGs for -3, -1 1/3, 0, +1 1/3, +3; out-of-range values
     and profile index draw no value; our code stores only to the framebuffer and the stack.
Also: the source reassembles to EV_CODE; each EV build differs from its base build only in the
regions setting / evkey / evtimer; the only references into the new code are the debug-shell
registration of `setting`, the key-handler branch and the event-table word.

Usage:  work/venv/bin/python tools/m8_evtest.py [-v] [--out DIR]
Needs:  unicorn, capstone, Pillow (work/venv); clang
"""
import os, struct, sys
from unicorn import UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_PC

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import m8_dngpatch as d
import m8_asm
import m8_infotest as it
import m8_inforender as ir
from m8_emutest import Emu, Escape, Results, STACK, SVC_MAGIC
from m8_rawpatch import PXA_BASE

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
SRC = os.path.join(ROOT, 'tools/asm/m8_infoev.s')
LO, HI = d.REGIONS['setting']
# every build with --dial-ev vs the same build without it; the screen code region the key handler runs
SCREEN_REGION = {None: 'memtest12', 'info': 'memtest12', 'profiles': 'bfmem'}
SETTINGS = 0x14C910                 # the settings block's address on the camera ([0xD4230])
APP = it.APP
DIALOG = APP + 0x70A4
EV_STEP, ARM, DLG_KEY, LEICA_TIMER, REDRAW = 0x65954, 0x8BD10, 0x61868, 0x43B64, 0x9A47C
DLG_VT = 0xED1B8

# name -> (service word, app+0x25741, app+0x254A8, [0x169CC8], app+0x2548C, app+0x254B0, override,
#          app+0x2548D, app+0x25744)
STATES = {
    'normal':  (0, 0, 0, 0, 0, 0, 0xFFFF, 0, 0),
    'service': (SVC_MAGIC, 0, 0, 0, 0, 0, 0xFFFF, 0, 0),
    'simple':  (0, 1, 0, 0, 0, 0, 5, 0, 0),     # Simple mode: app+0x25741 and override profile 5
    'svc+simple': (SVC_MAGIC, 1, 0, 0, 0, 0, 5, 0, 0),
    '254a8':   (0, 0, 1, 0, 0, 0, 0xFFFF, 0, 0),
    '169cc8':  (0, 0, 0, 1, 0, 0, 0xFFFF, 0, 0),
    'a held':  (0, 0, 0, 0, 1, 0, 0xFFFF, 0, 0),        # app+0x2548C: `a` held
    'a held, svc': (SVC_MAGIC, 0, 0, 0, 1, 0, 0xFFFF, 0, 0),
    'a held, simple': (0, 1, 0, 0, 1, 0, 5, 0, 0),
    'a + S held': (0, 0, 0, 0, 1, 0, 0xFFFF, 1, 0),     # shutter fully pressed
    'a held, Leica dial on': (0, 0, 0, 0, 1, 0, 0xFFFF, 0, 1),   # body enabled evfastaccess
    '254b0':   (0, 0, 0, 0, 0, 1, 0xFFFF, 0, 0),
}
OPEN_STATES = ('normal', 'a held')


def settings_block(emu, idx=4, override=0xFFFF, ev=9):
    emu.w32(0xD4230, SETTINGS)
    emu.w16(SETTINGS, idx)
    emu.w16(SETTINGS + 2, override)
    for p in range(6):
        emu.w16(SETTINGS + 0x1C + 0x50 * p + 0x22, ev)


def run_key(pxa, region, state, key, unknown):
    """Application key handler with the INFO code and ev_key real; -> trace."""
    trace = []
    def rec(name):
        def fn(emu, r0, r1, r2):
            if name == 'arm':
                sp = emu.uc.reg_read(UC_ARM_REG_SP)
                trace.append(('call', 'arm', it.rel(r0), r1, r2, emu.uc.reg_read(UC_ARM_REG_R3),
                              emu.u32(sp), emu.u32(sp + 4), emu.u32(sp + 8)))
            else:
                trace.append(('call', name, it.rel(r0), it.rel(r1)))
            return 0
        fn.__name__ = name
        return fn
    stubs = {a: rec(n) for a, n in it.STUB_ADDRS.items()}
    stubs[EV_STEP] = rec('ev_step')
    stubs[ARM] = rec('arm')
    stubs[0x658B4] = rec('evfast_show')            # Leica's own dial window (0x45040 path)
    real = [(it.KEY_HANDLER, 0x450AC), (0x403F0, 0x40410), d.REGIONS[region], (LO, HI)]
    e = Emu(pxa, real=real, stubs=stubs, ram=0x200000)
    uc = e.uc
    uc.mem_map(APP, it.APP_SIZE)
    uc.mem_map(it.CTX2, it.APP_SIZE)
    e.w32(0xD4220, APP)
    e.w32(0xD421C, it.CTX2)
    svc, simple, b254a8, g169, b2548c, b254b0, override, b2548d, w25744 = STATES[state]
    uc.mem_write(APP + 0x2548D, bytes([b2548d]))
    e.w32(APP + 0x25744, w25744)
    e.w32(APP + 0x254A0, svc)
    uc.mem_write(APP + 0x25741, bytes([simple]))
    uc.mem_write(APP + 0x254A8, bytes([b254a8]))
    uc.mem_write(0x169CC8, bytes([g169]))
    uc.mem_write(APP + 0x2548C, bytes([b2548c]))
    uc.mem_write(APP + 0x254B0, bytes([b254b0]))
    settings_block(e, override=override)

    def on_write(uc_, access, addr, size, value, _):
        if not STACK <= addr < STACK + 0x10000:
            trace.append(('write', it.rel(addr), size, value & ((1 << 8 * size) - 1)))
    uc.hook_add(UC_HOOK_MEM_WRITE, on_write)
    try:
        ret = e.call(it.KEY_HANDLER, APP, key)
    except Escape as x:
        unknown.add(str(x))
        return trace + [('escape', str(x))]
    if e.uc.reg_read(UC_ARM_REG_SP) != STACK + 0xF000:
        trace.append(('sp not restored',))
    return trace + [('ret', ret)]


def method_env(pxa, vt, hidden=False):
    """Emulator for our page methods: dialog at its real place in a fake app; -> (emu, log)."""
    log = []
    def rec(name):
        def fn(emu, r0, r1, r2):
            if name == 'arm':
                sp = emu.uc.reg_read(UC_ARM_REG_SP)
                log.append(('arm', r0, r1, r2, emu.uc.reg_read(UC_ARM_REG_R3),
                            emu.u32(sp), emu.u32(sp + 4), emu.u32(sp + 8)))
            else:
                log.append((name, r0, r1))
            return 0
        fn.__name__ = name
        return fn
    stubs = {EV_STEP: rec('ev_step'), ARM: rec('arm'), DLG_KEY: rec('dialog_key'),
             LEICA_TIMER: rec('leica_timer'), REDRAW: rec('redraw')}
    e = Emu(pxa, real=[(LO, HI)], stubs=stubs, ram=0x200000)
    e.uc.mem_map(APP, it.APP_SIZE)
    e.w32(DIALOG, vt)
    e.w32(DIALOG + 0x20, 4 if hidden else 0)
    def on_write(uc_, access, addr, size, value, _):
        if not STACK <= addr < STACK + 0x10000:
            log.append(('write', addr, value & ((1 << 8 * size) - 1)))
    e.uc.hook_add(UC_HOOK_MEM_WRITE, on_write)
    return e, log


def step_once(pxa, start, key, idx=4, override=0xFFFF):
    """Leica's 0x65954 for real -> (value now in the active profile, settings [0], [2], posts)."""
    posts = []
    MSG = 0x180000
    def alloc(emu, r0, r1, r2):
        return MSG
    def post(emu, r0, r1, r2):
        posts.append((r0, emu.u32(r1), emu.u32(r1 + 4)))
        return 0
    alloc.__name__, post.__name__ = 'msg_alloc', 'msg_post'
    e = Emu(pxa, real=[(0x65954, 0x65A50), (0x219D4, 0x219F0)],
            stubs={0x8B838: alloc, 0x8B6F4: post}, ram=0x200000)
    e.w32(0xD4208, 0x111)
    e.w32(0xD420C, 0x222)
    settings_block(e, idx=idx, override=override, ev=start)
    e.call(0x65954, 0, key)
    return e.u16(e.profile() + 0x22), e.u16(SETTINGS), e.u16(SETTINGS + 2), posts


def main():
    verbose = '-v' in sys.argv
    out = sys.argv[sys.argv.index('--out') + 1] if '--out' in sys.argv else os.path.join(ROOT, 'work')
    os.makedirs(out, exist_ok=True)
    res = Results(verbose)
    stock = open(os.path.join(ROOT, 'firmware/stock/m8-2_014.upd'), 'rb').read()
    res.check('m8_infoev.s reassembles to EV_CODE', m8_asm.assemble(SRC) == d.EV_CODE, True)
    ir.SRC, ir.LO, ir.HI = SRC, LO, HI
    sym = ir.symbols()
    vt_ev = LO + sym['vt_ev']
    res.check('entry addresses in the patch script match the source',
              (d.EV_KEY_ENTRY, d.EV_TIMER_ENTRY), (LO + sym['ev_key'], LO + sym['ev_timer']))
    vt_words = list(struct.unpack('<22I', d.EV_CODE[sym['vt_ev']:sym['vt_ev'] + 88]))
    p4 = d.pxa_of(d.build_file(stock, 'dng')[1])
    leica_vt = list(struct.unpack_from('<22I', p4, DLG_VT - PXA_BASE))
    res.check('vt_ev = the dialog\'s own table except keys / title / content',
              [i for i in range(22) if vt_words[i] != leica_vt[i]], [11, 18, 19])

    common = [('call', 'dance_tracker', 'app+0x0', None), ('call', 'f_41788', 'app+0x0', '0x0'),
              ('call', 'timer_stop', 'app+0x2534C', '0x0')]
    unknown = set()
    pxas = {}
    for option, screen, dial, meta in d.BUILDS:
        if not dial:
            continue
        region = SCREEN_REGION[screen]
        n, base_n = d.build_name(option, screen, True, meta), d.build_name(option, screen, False, meta)
        _, sn = d.build_file(stock, option, screen, True, meta)
        _, sb = d.build_file(stock, option, screen, False, meta)
        pn, pb = d.pxa_of(sn), d.pxa_of(sb)
        pxas[option, screen, meta] = pn
        pos, size = d.find_lump(sn, b'PXA')
        res.check(f'{n} vs {base_n}: changes only in setting + evkey + evheld + evtimer',
                  d.region_violations(sb, sn, pos, size, {'setting', 'evkey', 'evheld', 'evtimer'}), [])
        res.check(f'{n}: references into the new code', it.refs_into(pn, LO, HI),
                  ['word 0x2DF2C = 0x2C44C', 'branch 0x44E9C', 'branch 0x44EDC',
                   f'word 0xEF2B4 = 0x{d.EV_TIMER_ENTRY:X}'])
        for state in STATES:
            diffs = []
            for key in range(256):
                tb, tn = run_key(pb, region, state, key, unknown), run_key(pn, region, state, key, unknown)
                if tb != tn:
                    diffs.append((key, tb, tn))
            if state not in OPEN_STATES:
                res.check(f'{n}, {state}: identical to {base_n} for all 256 keys',
                          [chr(k) for k, _, _ in diffs], [])
                continue
            res.check(f'{n}, {state}: only the dial keys differ', [chr(k) for k, _, _ in diffs], ['c', 'w'])
            for key, tb, tn in diffs:
                k = len(tb) - 1                         # the base build's trace, without its return
                step = tn[k] if len(tn) > k and tn[k][:2] == ('call', 'ev_step') else None
                want = tb[:-1] + [('call', 'ev_step', step[2] if step else '?', '0x%X' % key),
                                  ('call', 'show_page', 'app+0x6F6C', '0x1'),
                                  ('write', 'app+0x2548F', 1, 1),
                                  ('write', 'app+0x7005', 1, 0),
                                  ('write', 'app+0x70A4', 4, vt_ev),
                                  ('call', 'show', 'app+0x70A4', '0x1'),
                                  ('call', 'arm', 'app+0x7104', 0x271E, 2000, 1, 0, 0, 0)]
                res.check(f'{n}, {state}, `{chr(key)}`: base build makes no call or write for it',
                          [x for x in tb[:-1] if x[0] == 'write' or x[1] not in ('dance_tracker', 'f_41788', 'timer_stop')], [])
                res.check(f'{n}, {state}, `{chr(key)}`: as base, then step, open as INFO does, arm the timer',
                          (tn[:-1], tn[-1] == tb[-1]), (want, True))

    pxa = pxas['dng', 'info', True]           # dng --meta --info --dial-ev (ev piece as camera-tested in 1254b743)
    # ---- 2. keys while the page is open
    bad = []
    for key in range(256):
        e, log = method_env(pxa, vt_ev)
        e.call(LO + sym['on_key'], DIALOG, key)
        if key in (ord('c'), ord('w')):
            want = [('ev_step', DIALOG, key), ('arm', DIALOG + 0x60, 0x271E, 2000, 1, 0, 0, 0),
                    ('redraw', DIALOG, ord('u'))]
        else:
            want = [('write', DIALOG, DLG_VT), ('dialog_key', DIALOG, ord('s'))]
        if log != want:
            bad.append((key, log))
    res.check('page open: dial steps + re-arms + redraws; every other key closes (own table, SET path)', bad, [])

    # ---- 3. timer event
    def timer(vt, hidden=False):
        e, log = method_env(pxa, vt, hidden)
        r = e.call(LO + sym['ev_timer'], APP, 0x271E)
        return log, e.u32(DIALOG)
    res.check('timer, our page open: closed as by SET', timer(vt_ev),
              ([('write', DIALOG, DLG_VT), ('dialog_key', DIALOG, ord('s'))], DLG_VT))
    res.check('timer, our page already hidden: only the method table restored', timer(vt_ev, True),
              ([('write', DIALOG, DLG_VT)], DLG_VT))
    res.check('timer, Leica\'s Body Debug Data: Leica\'s handler, same args, no writes', timer(DLG_VT),
              ([('leica_timer', APP, 0x271E)], DLG_VT))
    ir.configure(13)
    vt_info = ir.LO + ir.symbols()['vt_info']
    ir.SRC, ir.LO, ir.HI = SRC, LO, HI
    res.check('timer, INFO page open: Leica\'s handler, no writes', timer(vt_info),
              ([('leica_timer', APP, 0x271E)], vt_info))

    # ---- 4. Leica's dial step
    got, want = [], []
    for start in range(19):
        for key, delta in ((ord('w'), 1), (ord('c'), -1)):
            got.append(step_once(pxa, start, key))
            want.append((min(18, max(0, start + delta)), 4, 0xFFFF, [(0x111, 0x2715, 0x222)]))
    res.check('Leica 0x65954: w +1, c -1, clamped 0..18, working profile 4, event 0x2715 posted', got, want)
    v, i0, i2, _ = step_once(pxa, 9, ord('w'), idx=1)
    res.check('Leica 0x65954 from user profile 1: switches to the working profile 4 (as the SET menu)',
              (v, i0, i2), (10, 4, 0xFFFF))

    # ---- 5. paint
    gui = ir.gui_lumps(stock)
    for label, ev, idx in [('m3', 0, 4), ('m1-1-3', 5, 4), ('0', 9, 4), ('p1-1-3', 13, 4), ('p3', 18, 4),
                           ('bad-value', 25, 4), ('bad-profile', 9, 7)]:
        s = ir.Screen(pxa, gui, None)
        settings_block(s.e, idx=idx, ev=ev)
        s.e.w32(ir.DLG, vt_ev)
        try:
            s.call(0x605AC, ir.DLG)
            err = None
        except Escape as x:
            err = str(x)
        res.check(f'paint {label}: runs to the end', err, None)
        res.check(f'paint {label}: our stores only framebuffer / stack', s.bad_writes[:5], [])
        fb = s.e.read(ir.FB, 320 * 240 * 3)
        mid = fb[70 * 960:105 * 960]                            # value text rows
        res.check(f'paint {label}: value drawn', any(mid), not label.startswith('bad'))
        s.png(os.path.join(out, f'ev-{label}.png'))
    res.check('no escapes to unlisted firmware code', sorted(unknown), [])
    print(f'{res.ok} passed, {res.bad} failed; PNGs in {out}')
    sys.exit(1 if res.bad else 0)


if __name__ == '__main__':
    main()
