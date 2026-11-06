#!/usr/bin/env python3
"""
Emulated RAW save path for the M8 2.014 PXA image: a simulated shutter press runs the real
store routine and the real per-block DMA handler, and the bytes that reach the "card" are
compared with a real camera file. Base for testing every m8_dngpatch.py stage before flashing.

What runs as firmware code (REAL): store routine 0x3CA58 (file type, name, RAW header,
DMA set-up), DMA chain builder 0x3BFBC, DMA start 0x3C1E0, block handler 0x3C900, RAW
producer 0x3C5A4, ring buffer set-up/get-space/commit 0x3C454-0x3C5A4 and 0x3D2A0, string
append 0x8817C, 0x90064, memcpy 0xB3F78, division/modulo 0xCAB28-0xCAE54.

Modelled in Python (everything else is a test failure):
  - eCos mutex/flag calls (0xB4998, 0xB49A0, 0xB48BC setbits, 0xB48C0 maskbits, 0xB48C4 wait);
  - 0x3B430 (review image + per-shot neutral from the Blackfin; reached only through --meta's
    raw_fetch): writes the sample JPG's neutral to job+0xD0 as 16384/x, as 0x3B430 does. Before
    it the job holds the default 7000/10000 seen on the camera (L1032091-94);
  - sensor/FPGA/GPIO control (0x94E2C, 0x94E98, 0x90C40, 0x90F2C), event post 0x98324,
    0x716F8 (resets the ctx+0x47C0 object at end of file);
  - geometry 0x90F9C (cached sensor table): RAW = 3976 samples x 2647 rows;
  - the PXA DMA controller: when DCSR1 gets RUN, the descriptor chain at DDADR1 is walked
    and filled from the "sensor" stream = the sample file's bytes after its header
    (0xEE once the stream is used up);
  - the card-writer task (writer object [ctx+0x4210] vtable +8 open, +0xC start,
    +0x10 kick, +0x18 finish): drains the ring exactly like 0x3D00C (contiguous readable),
    0x3D050 (consume, sets flag bit 0 once free >= margin) and 0x3D714 (rounding modes).
    Drain policies vary when it runs; the file must come out the same under all of them.

Checks (per build): file bytes vs the camera sample, file name, ring/DMA bookkeeping,
writes by the header block (0x3CE48-0x3CF20) same addresses as the flashed build, call
trace identical to the flashed build, only the type-0 path reaches the header block,
settings loader/menu unchanged (m8_emutest), declared-regions diff (m8_dngpatch).

Usage:  work/venv/bin/python tools/m8_savetest.py [--build raw-menu|dng [--meta] [--info|--profiles] [--dial-ev]]
                                                  [--stage N] [--quick] [-v]
        (default: the 4 offered builds, dng --meta [--info] [--dial-ev], + the diagnostic builds 1, 2,
        3, 5 (canary, idle, dump, .DNG names); --build: any build, also earlier ones;
        --stage N: a history / old stage number; --quick = one sample, one drain policy)
"""
import hashlib, os, struct, sys, time
from unicorn import UC_HOOK_MEM_WRITE, UC_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_PC

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import m8_asm
import m8_dngpatch as dp
import m8_emutest as et
import m8_wrapdng as wd
from m8_rawpatch import PXA_BASE

ROOT = et.ROOT
SAMPLES = ['sampleraws/L1031902.RAW', 'sampleraws/L1031901.RAW']
RAW_SIZE, HDR, ROW_BYTES, ROWS = 21_049_052, 108, 3976 * 2, 2647

STORE = 0x3CA58             # event 0x2AFB handler (0xEF110): store one image file
BLOCK_DONE = 0x3C900        # event 0x2AFA handler (0xEF100): DMA block complete
RING_SETUP = 0x3D2A0        # ctx vtable +0x3C: ring at 0x2841D00, 8 MB raw, align 0x200, margin 0x40000
REAL = [
    (0x3BFBC, 0x3C1F4),     # DMA chain builder, DMA start
    (0x3C454, 0x3C6B0),     # ring set-up, get-space, commit, RAW producer
    (0x3C900, 0x3D00C),     # block handler, store routine
    (0x3D2A0, 0x3D2D4),     # ring set-up method
    (0x8817C, 0x881F0),     # string append (file name)
    (0x90064, 0x9006C),     # returns 0
    (0xB3F78, 0xB40D4),     # memcpy
    (0xCAB28, 0xCAE54),     # unsigned divide / modulo
    (0x28AB0, dp.TABLES),   # new code (stages 3-5 reach it through the hook)
    dp.REGIONS['jpgipp'],   # new code of --meta (meta_hook; its tables are in ldimg)
]
HEADER = dp.REGIONS['header']
NEW_CODE = (0x28AB0, dp.TABLES)
META_CODE = dp.REGIONS['jpgipp']
META_LITERAL = META_CODE[0] + dp.META_CODE.index(struct.pack('<I', dp.META_TABLES))   # meta_hook's `ldr r8, =TABLES`
PLAY_LITERAL = META_CODE[0] + dp.PLAY_CODE.index(struct.pack('<I', dp.META_TABLES))   # the same in --play's code
TRAIL_OFF = HDR + (ROWS - 1) * ROW_BYTES     # file offset of the trailing sensor row (IFD0)
DMA_BUF, DMA_PARITY = 0x026F44E0, 0x3E080   # CPU view of the DMA target (phys 0xA26F44E0)
PHYS = 0xA0000000                            # SDRAM physical base; CPU address = phys - PHYS
DCSR1, DDADR1, OSCR = 0x40000004, 0x40000210, 0x40A00010

# Emulator memory (inside the 64 MB RAM map; the firmware's own addresses are below 0x3042000)
CTX, JOB, MSG, W, W_VTBL = 0x3800000, 0x3810000, 0x3811000, 0x3812000, 0x3812100
NAMEBUF, DIRSTR, STATUS = 0x3813000, 0x3814000, 0x3815000
RAM = 0x4000000
SENT = et.SENTINEL + 0x200                   # writer vtable slot stubs
RING = CTX + 0x46C8
DIR = b'\\DCIM\\100LEICA\\L1031902.'

POLICIES = {  # card-writer behaviour: (unit drained on kick or None, unit drained when the producer waits)
    'eager':  (1, 1),               # writer keeps up completely
    'sector': (0x2000, 0x2000),     # 0x3D714 mode 1 on every kick
    'big':    (0x20C000, 0x2000),   # 0x3D714 mode 0 on kick; mode 1 when the ring is full
    'lazy':   (None, 0x2000),       # writer only runs when the producer blocks on a full ring
}


class Writer:
    """Card-writer task model over the firmware's ring struct (FINDINGS "Image save path")."""
    def __init__(self, e, policy):
        self.e, self.kick_unit, self.wait_unit = e, *POLICIES[policy]
        self.out = bytearray()
        self.waits = self.kicks = 0

    def f(self, off): return self.e.u32(RING + off)

    def readable(self):             # 0x3D00C
        count, w, r = self.f(0x18), self.f(0x10), self.f(0x14)
        if count == 0:
            return 0
        return w - r if w > r else self.f(8) - r

    def consume(self, n):           # 0x3D050
        e, size = self.e, self.f(8)
        e.w32(RING + 0x14, (self.f(0x14) + n) % size)
        e.w32(RING + 0x18, self.f(0x18) - n)
        if size - self.f(0x18) >= self.f(0xC):
            e.w32(RING + 0x20, e.u32(RING + 0x20) | 1)

    def drain(self, unit, until_space=False):   # 0x3D714: whole units unless the readable part wraps
        while True:
            if until_space and self.e.u32(RING + 0x20) & 1:
                return
            n = self.readable()
            if self.f(0x14) < self.f(0x10):     # 0x3D0C0 = 0: not wrapped -> round down
                n -= n % unit
            if n == 0:
                return
            self.out += self.e.read(self.f(0) + self.f(0x14), n)
            self.consume(n)

    def kick(self, e, *_):
        self.kicks += 1
        if self.kick_unit:
            self.drain(self.kick_unit)

    def wait(self, e, flag, pattern, mode):     # 0xB48C4 cyg_flag_wait(flag, 1, OR)
        assert (pattern, mode) == (1, 2), (pattern, mode)
        if not e.u32(flag) & pattern:
            self.waits += 1
            self.drain(self.wait_unit, until_space=True)
            assert e.u32(flag) & pattern, 'writer could not free space'
        return pattern

    def finish(self, e, *_):
        self.drain(1)


class SaveEmu(et.Emu):
    def __init__(self, pxa, sensor, policy='eager'):
        self.writer = Writer(self, policy)
        stubs = {
            0xB4998: 'mutex_lock', 0xB49A0: 'mutex_unlock',
            0xB48BC: flag_set, 0xB48C0: flag_mask, 0xB48C4: self.writer.wait,
            0x98324: 'event_post_98324', 0x94E2C: 'sensor_94e2c', 0x94E98: 'sensor_94e98',
            0x90C40: 'fpga_cmd_90c40', 0x90F2C: 'fpga_90f2c', 0x716F8: 'reset_obj_716f8',
            0x90F9C: geometry, 0x3B430: wb_fetch,
            SENT + 0x08: 'writer_open', SENT + 0x0C: 'writer_start',
            SENT + 0x10: self.writer.kick, SENT + 0x18: self.writer.finish,
        }
        super().__init__(pxa, real=REAL, stubs=stubs, ram=RAM, per_block=True)
        uc = self.uc
        uc.mem_map(0x40000000, 0x1000)          # DMA controller
        uc.mem_map(0x40A00000, 0x1000)          # OS timer
        uc.hook_add(UC_HOOK_MEM_WRITE, self._dma, begin=DCSR1, end=DCSR1 + 3)
        self.sensor, self.spos = sensor, 0
        self.chains = []                        # bytes moved per DMA run
        self.hdr_writes = []                    # (address, size, value) written by the header block
        self.new_writes = []                    # (address, size) written by the new code
        self.new_entries = 0                    # times dng_hook was entered

    def _code(self, uc, addr, size, _):
        if addr in (dp.HOOK_ENTRY, dp.META_ENTRY):
            self.new_entries += 1
        super()._code(uc, addr, size, _)

    def _align(self, uc, access, addr, size, value, _):
        super()._align(uc, access, addr, size, value, _)
        if access == UC_MEM_WRITE:
            pc = uc.reg_read(UC_ARM_REG_PC)
            if HEADER[0] <= pc < HEADER[1]:
                self.hdr_writes.append((addr, size, value & ((1 << 8 * size) - 1)))
            elif NEW_CODE[0] <= pc < NEW_CODE[1] or META_CODE[0] <= pc < META_CODE[1]:
                self.new_writes.append((addr, size))

    def _dma(self, uc, access, addr, size, value, _):
        if not value & 0x80000000:              # RUN
            return
        desc, moved = self.u32(DDADR1) - PHYS, 0
        while True:
            nxt, src, dst, cmd = struct.unpack('<4I', self.read(desc, 16))
            n = cmd & 0x1FFF
            chunk = self.sensor[self.spos:self.spos + n]
            self.spos += len(chunk)
            uc.mem_write(dst - PHYS, chunk + b'\xEE' * (n - len(chunk)))
            moved += n
            if nxt & 1:                         # STOP
                break
            desc = nxt - PHYS
        self.chains.append(moved)


def flag_set(e, flag, bits, _):
    e.w32(flag, e.u32(flag) | bits)


def flag_mask(e, flag, mask, _):
    e.w32(flag, e.u32(flag) & mask)


def geometry(e, out, typ, size):
    assert typ == 0, f'geometry for type {typ}'
    e.w32(out, ROW_BYTES // 2)
    e.w32(out + 4, ROWS)
    return out


def save(pxa, raw, policy='eager', bia=False, iso=None):
    """One simulated RAW (or .BIA) shot through the real save path; returns the emulator."""
    e = SaveEmu(pxa, raw[HDR:], policy)
    # ring buffer, set up by the firmware's own method
    e.call(RING_SETUP, CTX, RING)
    # capture context: file-name buffer {ptr, len, cap}, writer object
    e.w32(CTX + 0x463C, NAMEBUF)
    e.w32(CTX + 0x4644, 0x100)
    e.w32(W, W_VTBL)
    for slot in (0x08, 0x0C, 0x10, 0x18):
        e.w32(W_VTBL + slot, SENT + slot)
    # job: RAW flag (+0x154) or BIA flag (+0x21A), directory/stem, header fields
    e.uc.mem_write(DIRSTR, DIR + b'\0')
    e.w32(JOB + 0x17C, DIRSTR)
    e.w32(JOB + 0x180, len(DIR))
    e.w32(JOB + 0x154, 1)
    e.uc.mem_write(JOB + 0x21A, bytes([bia]))
    e.w32(JOB + 0x210, 1)                       # header u16[16..18] (sample: 1, 0, 0)
    fill_shot(e, iso)
    e.uc.mem_write(JOB + 0xD0, JOB_DEFAULT_WB)  # filled only by 0x3B430
    # message: +8 job, +0xC writer
    e.w32(MSG + 8, JOB)
    e.w32(MSG + 0xC, W)
    r = e.call(STORE, CTX, MSG)
    assert r == 0, f'store routine returned {r}'
    e.blocks = e.u32(CTX + 0x421C)
    e.w32(STATUS + 8, 4)                        # DCSR ENDINTR
    for i in range(e.blocks):
        assert e.u32(CTX + 0x4220) == i
        e.call(BLOCK_DONE, CTX, STATUS)
    e.name = e.cstr(NAMEBUF)
    e.done = e.read(CTX + 0x4630, 1)[0]
    return e


# Per-shot values for metadata struct S (job+4), at the offsets the slot table assumes: those of
# the sample pair's JPG. The firmware's real offsets are what stage 3 checks on the camera.
SHOT = {'DateTime': '0000:00:00 00:00:00', **wd.jpg_shot(os.path.join(ROOT, 'sampleraws/L1031902.JPG'))}
SHOT.update(DateTimeOriginal=SHOT['DateTime'], DateTimeDigitized=SHOT['DateTime'])
SETTINGS_PATTERN = bytes(range(256)) * 2        # stands in for the settings block (dump check)
# --meta: the sample JPG's MakerNote values, put into S at the offsets 0x6D400 reads them from
MAKER = wd.tiff_maker(wd.jpg_tiff(os.path.join(ROOT, 'sampleraws/L1031902.JPG')))


JOB_DEFAULT_WB = struct.pack('<6I', 7000, 10000, 10000, 10000, 7000, 10000)   # job+0xD0 before 0x3B430
# --play: the review image 0x3B430 leaves in buffer [job+0x26C] (stand-in: the sample JPG at 320x240)
REVIEW_BUF, REVIEW_SIZE, REVIEW_DONE = 0x24C1CE0, 0x38400, 0x169BD4
from PIL import Image
REVIEW_IMG = Image.open(os.path.join(ROOT, 'sampleraws/L1031902.JPG')).convert('RGB').resize(
    (wd.REVIEW_W, wd.REVIEW_H), Image.BOX).tobytes()


def put_review(e, idx, done=True):
    e.uc.mem_write(REVIEW_BUF + idx * REVIEW_SIZE, REVIEW_IMG)
    e.uc.mem_write(REVIEW_DONE + idx, bytes([done]))


def wb_fetch(e, job, kind, _):
    """0x3B430(job, kind): the neutral as the Blackfin returns it (the sample JPG's 0x322-0x324)."""
    assert (job, kind) == (JOB, 0), f'0x3B430({job:#x}, {kind})'
    e.uc.mem_write(JOB + 0xD0, b''.join(MAKER[t][1] for t in (0x322, 0x323, 0x324)))
    idx = e.u32(JOB + 0x26C)
    put_review(e, idx)
    return REVIEW_BUF + idx * REVIEW_SIZE       # review image (the RAW path ignores it)


def fill_shot(e, iso=None, maker=MAKER):
    shot = dict(SHOT, **({} if iso is None else {'ISO': iso}))
    for _, name, typ, _, where in wd.SHOT_FIELDS:
        if where.startswith('S+'):
            e.uc.mem_write(JOB + 4 + int(where[2:], 16), wd.pack_value(typ, shot[name]))
    for tag, _, off, n in wd.MAKER_FIELDS:
        e.uc.mem_write(JOB + 4 + off, maker[tag][1][:n])
    e.uc.mem_write(e.u32(et.P_SETTINGS), SETTINGS_PATTERN)


def trail_dest(idx):
    """CPU address of the trailing row in the last block's DMA buffer."""
    return DMA_BUF + (idx & 1) * DMA_PARITY + (ROWS - idx * 16) * ROW_BYTES - ROW_BYTES


def expected(pieces, raw, e, iso=None):
    """The file a build with these pieces must produce from `raw`, computed without the firmware."""
    out = bytearray(raw)
    if 'canary' in pieces:
        out[6] = 0xA5
    if 'ldimg (dump)' in pieces:
        last = e.blocks - 1
        ctx = bytearray(e.read(CTX + 0x4210, 0x30))
        ctx[0x10:0x14] = struct.pack('<I', last)           # block index at hook time
        dump = (b'M8DUMP01' + bytes(ctx) +
                struct.pack('<4I', CTX, trail_dest(last), (ROWS - last * 16) * ROW_BYTES, last))
        dump = dump.ljust(0x80, b'\xEE')                  # 0x48..0x7F untouched (sensor bytes)
        dump = dump[:0x48] + raw[TRAIL_OFF + 0x48:TRAIL_OFF + 0x80] + e.read(JOB, 0x280) + \
            e.read(e.u32(et.P_SETTINGS), 0x200)
        out[TRAIL_OFF:TRAIL_OFF + len(dump)] = dump
    if 'tiff' in pieces:
        play = 'play' in pieces
        t, slots = dp.play_template() if play else dp.meta_template() if 'meta' in pieces else dp.template()
        shot = {k: v for k, v in SHOT.items() if k in dict(
            (n, 1) for n, *_ in dp.slot_table())}
        if iso is not None:
            shot['ISO'] = iso
        shot['BlackLevel'] = wd.black_for_iso(shot['ISO'])
        if 'meta' in pieces or play:                      # MakerNote + AsShotNeutral from the JPG's values
            t = wd.fill_maker(t, slots, MAKER)
        out = bytearray(wd.wrap(raw, t, slots, shot))
        if play:                                          # IFD0 image: sampled from the review image
            off, n, _ = slots['Thumbnail']
            out[TRAIL_OFF + off:TRAIL_OFF + off + n] = wd.thumbnail(REVIEW_IMG)
        elif 'meta' in pieces:                            # job+0..0x280 behind the template
            out[TRAIL_OFF + dp.DUMP_AT:TRAIL_OFF + dp.DUMP_AT + dp.DUMP_LEN] = e.read(JOB, dp.DUMP_LEN)
    return bytes(out)


def run_hook(pxa, size, idx, bia=False, row_bytes=ROW_BYTES, rows=ROWS, iso=None, maker=MAKER,
             entry=dp.HOOK_ENTRY, review=None):
    """Call dng_hook directly as the producer's `bl` would; returns (emu, regs preserved?)."""
    from unicorn.arm_const import (UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
                                   UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
                                   UC_ARM_REG_SP, UC_ARM_REG_LR)
    e = SaveEmu(pxa, b'')
    e.w32(CTX + 0x4634, JOB)
    e.uc.mem_write(JOB + 0x21A, bytes([bia]))
    e.w32(CTX + 0x4220, idx)
    e.w32(CTX + 0x4228, row_bytes)
    e.w32(CTX + 0x422C, rows)
    fill_shot(e, iso, maker)
    if review is not None:                      # (review buffer index, review-done byte)
        e.w32(JOB + 0x26C, review[0])
        if review[0] < 10:
            put_review(e, review[0], review[1])
    keep = [UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R9,
            UC_ARM_REG_R10, UC_ARM_REG_R11, UC_ARM_REG_SP]
    vals = [CTX, 0x5555, 0x6666, size, 0x8888, 0x9999, 0xAAAA, 0xBBBB, et.STACK + 0xF000]
    for r, v in zip(keep, vals):
        e.uc.reg_write(r, v)
    e.uc.reg_write(UC_ARM_REG_LR, et.SENTINEL + 0xC0)    # dng_hook returns to lr - 0xC0
    e.uc.emu_start(entry, et.SENTINEL, count=100_000)
    if e.err or e.uc.reg_read(UC_ARM_REG_PC) != et.SENTINEL:
        raise et.Escape(e.err or 'dng_hook did not return')
    return e, [e.uc.reg_read(r) for r in keep] == vals


def run_fetch(pxa, typ, bia):
    """raw_fetch entered by 0x3CB98's `b` after `cmp type, #0`: (pc reached, regs kept, writes)."""
    from unicorn.arm_const import UC_ARM_REG_CPSR, UC_ARM_REG_R0, UC_ARM_REG_R2
    e = SaveEmu(pxa, b'')
    e.w32(CTX + 0x4634, JOB)
    e.uc.mem_write(JOB + 0x21A, bytes([bia]))
    regs = list(range(UC_ARM_REG_R0, UC_ARM_REG_R0 + 13))           # r0-r12
    vals = [0, JOB, 0x2222, typ, 0x4444, 0x5555, 0x4634, CTX, 0x8888, 0x9999, 0xAAAA, 0xBBBB, 0xCCCC]
    for r, v in zip(regs, vals):
        e.uc.reg_write(r, v)
    cpsr = e.uc.reg_read(UC_ARM_REG_CPSR) & ~(1 << 30)
    e.uc.reg_write(UC_ARM_REG_CPSR, cpsr | ((typ == 0) << 30))    # Z of `cmp type, #0`
    stop = 0x3CB9C if bia and typ == 0 else 0x3CC70
    e.uc.emu_start(dp.META_FETCH, stop, count=8)
    kept = [e.uc.reg_read(r) for r in regs]
    kept = kept[:1] + kept[2:] == vals[:1] + vals[2:]               # r1 is redefined on both paths
    return e.uc.reg_read(UC_ARM_REG_PC), kept, e.new_writes


def neutral(dng):
    """AsShotNeutral of a DNG as [(num, den)] x 3."""
    for k in range(struct.unpack_from('<H', dng, TRAIL_OFF)[0]):
        tag, typ, cnt, val = struct.unpack_from('<HHII', dng, TRAIL_OFF + 2 + 12 * k)
        if tag == 50728:
            return [struct.unpack_from('<II', dng, val + 8 * i) for i in range(cnt)]


def outside(writes, lo, hi):
    stack = (et.STACK, et.STACK + 0x10000)
    return [(hex(a), z) for a, z in writes
            if not (lo <= a and a + z <= hi) and not (stack[0] <= a and a + z <= stack[1])]


def branches_into(pxa, lo, hi):
    """Addresses of B/BL instructions (any condition) and literal words that target [lo, hi)
    from outside it."""
    out = []
    for o in range(0, len(pxa) - 3, 4):
        a = PXA_BASE + o
        if lo <= a < hi:
            continue
        w = struct.unpack_from('<I', pxa, o)[0]
        if (w >> 25) & 7 == 5 and w >> 28 != 0xF:
            off = w & 0xFFFFFF
            off -= (off & 0x800000) << 1
            if lo <= a + 8 + 4 * off < hi:
                out.append(a)
        elif lo <= w < hi:
            out.append(a)
    return out


def main():
    verbose = '-v' in sys.argv
    quick = '--quick' in sys.argv
    # targets: (label, pieces, title)
    targets = [(dp.build_name(*b), dp.build_pieces(*b), 'build ' + dp.build_name(*b))
               for b in dp.BUILDS]
    targets += [(f'stage{n}', dp.HISTORY[n][1], f'stage {n}: {dp.HISTORY[n][0]}') for n in (1, 2, 3, 5)]
    if '--stage' in sys.argv:
        n = int(sys.argv[sys.argv.index('--stage') + 1])
        targets = [(f'stage{n}', dp.stage_pieces(n), f'stage {n}')]
    if '--build' in sys.argv:
        i = sys.argv.index('--build') + 1
        flags = []
        while i < len(sys.argv) and sys.argv[i] in ('raw-menu', 'dng', '--meta', '--play', '--info', '--profiles', '--dial-ev'):
            flags.append(sys.argv[i])
            i += 1
        option = [f for f in flags if not f.startswith('--')]
        screen = [f[2:] for f in flags if f in ('--info', '--profiles')]
        if len(option) != 1 or len(screen) > 1:
            raise SystemExit(__doc__)
        b = (option[0], screen[0] if screen else None, '--dial-ev' in flags,
             'play' if '--play' in flags else '--meta' in flags)
        targets = [(dp.build_name(*b), dp.build_pieces(*b), 'build ' + dp.build_name(*b))]
    R = et.Results(verbose)
    stock_upd = open(os.path.join(ROOT, 'firmware/stock/m8-2_014.upd'), 'rb').read()
    stock = dp.pxa_of(stock_upd)
    flashed_upd = dp.flashed_file(stock_upd)
    flashed = dp.pxa_of(flashed_upd)
    R.check('flashed build file on disk = generated base',
            hashlib.sha256(open(os.path.join(ROOT, 'firmware/patched/m8-2_014.upd'), 'rb').read())
            .hexdigest(), dp.FLASHED_SHA)
    builds = {'stock': stock, 'flashed': flashed}
    for label, pieces, _ in targets:
        base, new = dp.pieces_file(stock_upd, pieces)    # refuses on a region violation
        builds[label] = dp.pxa_of(new)
        R.check(f'{label} declared regions vs flashed', dp.region_violations(
            base, new, *dp.find_lump(new, b'PXA'), dp.regions_of(pieces)), [])
    samples = SAMPLES[:1] if quick else SAMPLES
    policies = ['eager'] if quick else list(POLICIES)
    raws = {s: open(os.path.join(ROOT, s), 'rb').read() for s in samples}

    print('Save path: stock and flashed builds reproduce the camera files byte for byte')
    ref = {}
    for name in ('stock', 'flashed'):
        for s, raw in raws.items():
            for pol in policies if s == samples[0] else ['eager']:
                t = time.time()
                e = save(builds[name], raw, pol)
                out = bytes(e.writer.out)
                R.check(f'{name:7s} {os.path.basename(s)} {pol:6s} file == camera file '
                        f'({time.time() - t:.1f}s, {e.writer.kicks} kicks, {e.writer.waits} waits)',
                        (len(out), out == raw), (RAW_SIZE, True))
                if pol == 'eager':
                    ref[name, s] = e
    e = ref['flashed', samples[0]]
    R.check('file name', e.name, (DIR + b'RAW').decode())
    R.check('blocks / DMA bytes per block', (e.blocks, set(e.chains)), (166, {16 * ROW_BYTES}))
    R.check('DMA runs (one per block) / sensor bytes consumed', (len(e.chains), e.spos),
            (166, RAW_SIZE - HDR))
    R.check('end-of-file flag ctx+0x4630, ring empty', (e.done, e.u32(RING + 0x18)), (1, 0))
    R.check('ring geometry (base, size, margin)', (e.u32(RING), e.u32(RING + 8), e.u32(RING + 0xC)),
            (0x2841D00, 0x7C0000, 0x40000))
    R.check('stock/flashed call traces identical', ref['stock', samples[0]].calls == e.calls, True)
    # the header block is entered only from the type-0 (RAW/BIA) branch of the store routine
    R.check('branches/pointers into the header block', [hex(a) for a in branches_into(flashed, *HEADER)],
            ['0x3cd58'])

    R.check('frozen code == reassembled tools/asm sources',
            (m8_asm.assemble(os.path.join(ROOT, 'tools/asm/m8_header.s')) == dp.HEADER_TIFF,
             m8_asm.assemble(os.path.join(ROOT, 'tools/asm/m8_dnghook.s')) == dp.HOOK_CODE,
             m8_asm.assemble(os.path.join(ROOT, 'tools/asm/m8_dngmeta.s')) == dp.META_CODE,
             m8_asm.assemble(os.path.join(ROOT, 'tools/asm/m8_dngplay.s')) == dp.PLAY_CODE), (True,) * 4)
    R.check('branches/pointers into the new code (flashed build)',
            [hex(a) for a in branches_into(flashed, *NEW_CODE)], ['0x2dd48'])
    bia_ref = save(flashed, raws[samples[0]], 'eager', bia=True)

    for b, pieces, title in targets:
        pxa = builds[b]
        play = 'play' in pieces
        meta = 'meta' in pieces or play
        hooked = 'hook' in pieces or meta
        tpl = dp.play_template() if play else dp.meta_template() if meta else dp.template()
        print(title)
        R.check(f'{b} branches/pointers into the header block',
                [hex(a) for a in branches_into(pxa, *HEADER)], ['0x3cd58'])
        R.check(f'{b} branches/pointers into the new code',
                [hex(a) for a in branches_into(pxa, *NEW_CODE)],
                sorted(['0x2dd48'] + ['0x3c6a8'] * (hooked and not meta) + [hex(PLAY_LITERAL if play else META_LITERAL)] * meta))
        R.check(f'{b} branches/pointers into writejpgipp',
                [hex(a) for a in branches_into(pxa, *META_CODE)], ['0x2db8c'] + ['0x3c6a8', '0x3cb98'] * meta)
        ext = b'DNG' if 'ext' in pieces else b'RAW'
        for s, raw in raws.items():
            for pol in policies if s == samples[0] else ['eager']:
                e = save(pxa, raw, pol)
                out, want = bytes(e.writer.out), expected(pieces, raw, e)
                diff = dp.changed_ranges(out, want) if len(out) == len(want) else 'size'
                R.check(f'{b} {os.path.basename(s)} {pol:6s} file == expected '
                        f'({len(dp.changed_ranges(want, raw))} ranges differ from the camera file)', diff, [])
                trail = trail_dest(e.blocks - 1)
                R.check(f'{b} {os.path.basename(s)} {pol:6s} new code: runs, writes outside trailing row/stack',
                        (e.new_entries, outside(e.new_writes, trail, trail + ROW_BYTES)), (int(hooked), []))
                if pol == 'eager' and s == samples[0]:
                    f = ref['flashed', s]
                    R.check(f'{b} file name', e.name, (DIR + ext).decode())
                    R.check(f'{b} call trace == flashed' + (' + 0x3B430 before the transfer' if meta else ''),
                            ([c for c in e.calls if c != 'wb_fetch'] == f.calls, e.calls.count('wb_fetch'),
                             e.calls.index('wb_fetch') < e.calls.index('sensor_94e2c') if meta else True),
                            (True, int(meta), True))
                    R.check(f'{b} header-block write addresses == flashed ({len(f.hdr_writes)} writes)',
                            sorted((a, z) for a, z, _ in e.hdr_writes) == sorted((a, z) for a, z, _ in f.hdr_writes),
                            True)
                    R.check(f'{b} DMA/ring bookkeeping == flashed', e.chains == f.chains and
                            (e.blocks, e.spos, e.done) == (f.blocks, f.spos, f.done), True)
                    if 'tiff' in pieces:
                        t = wd.check(out, raw)
                        R.check(f'{b} DNG structure check (m8_wrapdng.check): IFD0 tags', t,
                                struct.unpack_from('<H', tpl[0])[0] if play else 35)
                    if meta:
                        R.check(f'{b} DNG MakerNote == the JPG\'s (18 tags, type and value)',
                                wd.tiff_maker(out) == MAKER and len(MAKER) == 18, True)
                        R.check(f'{b} DNG AsShotNeutral == JPG MakerNote 0x322-0x324',
                                neutral(out), [struct.unpack('<II', MAKER[t][1]) for t in (0x322, 0x323, 0x324)])
        # .BIA (dark frame): same type-0 header code, hook skips it
        e = save(pxa, raws[samples[0]], 'eager', bia=True)
        want = bytearray(bia_ref.writer.out)
        if 'canary' in pieces:
            want[6] = 0xA5
        if 'tiff' in pieces:
            want[:8] = b'II*\0' + struct.pack('<I', TRAIL_OFF)
        R.check(f'{b} .BIA: name, == expected, new-code runs, writes other than its stack, 0x3B430 calls',
                (e.name, bytes(e.writer.out) == bytes(want), e.new_entries, outside(e.new_writes, 0, 0),
                 e.calls.count('wb_fetch')), ((DIR + b'BIA').decode(), True, int(hooked), [], 0))
        # raw_fetch (--meta), run directly from 0x3CB98's flags: JPG / DNG and RAW -> 0x3CC70, .BIA -> 0x3CB9C
        if meta:
            got = [run_fetch(pxa, typ, bia) for typ, bia in ((1, 0), (2, 0), (0, 0), (0, 1), (1, 1))]
            R.check(f'{b} raw_fetch: (pc, regs kept, writes) for JPG, DNG, RAW, .BIA, JPG+BIA flag', got,
                    [(0x3CC70, True, [])] * 3 + [(0x3CB9C, True, []), (0x3CC70, True, [])])
        # dng_hook guards, called directly (the code is present from stage 2 on)
        if 'ldimg' in pieces or 'ldimg (dump)' in pieces or meta:
            entry = dp.META_ENTRY if meta else dp.HOOK_ENTRY
            last, size = 165, 7 * ROW_BYTES
            cases = [('normal', dict(size=size, idx=last), True),
                     ('even block', dict(size=size, idx=164), True),
                     ('.BIA flag', dict(size=size, idx=last, bia=True), False),
                     ('row bytes 7950', dict(size=size, idx=last, row_bytes=7950), False),
                     ('rows 2646', dict(size=size, idx=last, rows=2646), False),
                     ('size 0 (dest below buffer)', dict(size=0, idx=164), False),
                     ('size 1 MB (dest above buffer)', dict(size=0x100000, idx=last), False)]
            for label, kw, writes in cases:
                e, kept = run_hook(pxa, entry=entry, **kw)
                dst = trail_dest(kw['idx']) - (ROWS - kw['idx'] * 16) * ROW_BYTES + kw['size']
                dma = [w for w in e.new_writes if not et.STACK <= w[0] < et.STACK + 0x10000]
                R.check(f'{b} dng_hook {label}: regs kept, writes, outside trailing row',
                        (kept, bool(dma), outside(e.new_writes, dst, dst + ROW_BYTES)), (True, writes, []))
        # BlackLevel per ISO (mode 2): dng_hook called directly, value read from the trailing row
        if 'ldimg' in pieces or meta:
            boff = tpl[1]['BlackLevel'][0] if meta else dp.black_table()[0]
            bad = []
            for iso in (0, 100, 160, 226, 227, 320, 452, 453, 640, 905, 906, 1250, 1810, 1811, 2500, 6400):
                e, kept = run_hook(pxa, 7 * ROW_BYTES, 165, iso=iso, entry=entry)
                got = e.u16(trail_dest(165) + boff)
                if not kept or got != wd.black_for_iso(iso):
                    bad.append((iso, got, wd.black_for_iso(iso)))
            R.check(f'{b} BlackLevel for 16 ISOs == m8_wrapdng.black_for_iso (iso, got, want)', bad, [])
        # AsShotNeutral (--meta): the camera's neutral only in 0x3B430's form (16384/r, 16384/16384,
        # 16384/b, r and b non-zero); anything else (job default, a zero, another scale): daylight
        if meta:
            woff = tpl[1]['AsShotNeutral'][0]
            wb = list(struct.unpack('<6I', b''.join(MAKER[t][1] for t in (0x322, 0x323, 0x324))))
            day = [x for r in wd.DAYLIGHT_NEUTRAL for x in r]
            cases = [('the JPG\'s neutral', wb, True),
                     ('job default 7000/10000', list(struct.unpack('<6I', JOB_DEFAULT_WB)), False),
                     ('scale 8192', [8192, wb[1], 8192, 8192, 8192, wb[5]], False)]
            cases += [(f'word {k} zero', wb[:k] + [0] + wb[k + 1:], False) for k in range(6)]
            cases += [(f'word {k} = 16385', wb[:k] + [16385] + wb[k + 1:], False) for k in (0, 2, 3, 4)]
            for label, words, camera in cases:
                m = dict(MAKER)
                m.update({t: (5, struct.pack('<2I', *words[2 * i:2 * i + 2]))
                          for i, t in enumerate((0x322, 0x323, 0x324))})
                e, kept = run_hook(pxa, 7 * ROW_BYTES, 165, maker=m, entry=entry)
                got = list(struct.unpack('<6I', e.read(trail_dest(165) + woff, 24)))
                R.check(f'{b} AsShotNeutral, {label}: ' + ('camera neutral' if camera else 'daylight'),
                        (kept, got), (True, words if camera else day))
        # IFD0 image (--play): every 8th pixel / row of review buffer [job+0x26C], only if its review-done
        # byte is set and the index is a buffer (0..9); otherwise nothing is written there (stays black)
        if play:
            toff, tn, _ = tpl[1]['Thumbnail']
            dst = trail_dest(165)
            for label, review, want in [('buffer 0', (0, 1), True), ('buffer 9', (9, 1), True),
                                        ('review-done byte 0', (3, 0), False), ('index 10', (10, 1), False),
                                        ('index 0xFFFFFFFF', (0xFFFFFFFF, 1), False)]:
                e, kept = run_hook(pxa, 7 * ROW_BYTES, 165, entry=entry, review=review)
                got = e.read(dst + toff, tn)
                R.check(f'{b} IFD0 image, {label}: regs kept, ' + ('== review image sampled' if want else 'not written')
                        + ', writes inside the trailing row',
                        (kept, got == (wd.thumbnail(REVIEW_IMG) if want else bytes(tn)),
                         outside(e.new_writes, dst, dst + ROW_BYTES)), (True, True, []))
        if 'tiff' in pieces:
            raw = raws[samples[0]]
            e = save(pxa, raw, 'eager', iso=1250)
            out, want = bytes(e.writer.out), expected(pieces, raw, e, iso=1250)
            R.check(f'{b} ISO 1250 shot == expected (BlackLevel 736)',
                    (dp.changed_ranges(out, want), e.new_entries, outside(e.new_writes, trail_dest(165),
                     trail_dest(165) + ROW_BYTES)), ([], 1, []))
        # settings loader and SET menu behave exactly as on the flashed build
        same = all(et.run_loader(pxa, et.make_eeprom(pxa, p, c))['block'] ==
                   et.run_loader(flashed, et.make_eeprom(flashed, p, c))['block']
                   for p in (1, 3) for c in range(6))
        same &= all(et.run_menu(pxa, 1, c, sv) == et.run_menu(flashed, 1, c, sv)
                    for c in range(6) for sv in (False, True))
        R.check(f'{b} settings loader + SET menu == flashed', same, True)

    print(f'{R.ok} passed, {R.bad} failed')
    sys.exit(1 if R.bad else 0)


if __name__ == '__main__':
    main()
