@ In-camera DNG with per-shot white balance and the Leica MakerNote (`dng --meta`). The code of
@ m8_dnghook.s (camera-tested in `dng`), moved into the `writejpgipp` debug command
@ (0x26D98-0x27090, FINDINGS "Reclaimable space") so that `ldimg` (0x28AB0-0x29104) can hold
@ the larger tables and template. Position independent apart from the tables literal.
@
@ 0x26D98  writejpgipp handler entry, now a stub that returns 0 (registration untouched).
@ 0x26DA0  meta_hook: reached by `bl` from the RAW producer's last-block branch (0x3C6A8, stock
@          `b 0x3C5EC`), once per RAW/BIA file. Returns to 0x3C5EC with every register except
@          r0-r3, ip, lr as it found them.
@
@ What it does (tables at 0x28AB8, after the ldimg stub; m8_dngpatch.meta_blob()):
@   the same guards as dng_hook (.BIA, geometry 3976 x 2647, destination inside the DMA buffer);
@   copy the IFD template (IFD0, EXIF IFD, MakerNote) into the trailing row, fill each slot from
@   the job (S = job+4: date, exposure, ISO ... and the MakerNote values; one slot also copies
@   job+0..0x280 behind the template for checking on the desktop), BlackLevel per ISO as dng_hook;
@   then AsShotNeutral = the camera's per-shot neutral (S+0xCC, three rationals, the values of the
@   JPG's MakerNote 0x322-0x324), only if none of its six words is zero -- otherwise the template's
@   daylight neutral stays; then clean + invalidate the D-cache lines, drain the write buffer.
@ Every store goes through `.Lcopy` (trailing row and DMA buffer only). Reads: ctx, job, own
@ tables. No calls to any firmware function.

    .syntax unified
    .arm
    .text

    .equ DMA_BUF,  0x026F44E0       @ CPU address of DMA buffer 0 (phys 0xA26F44E0)
    .equ DMA_ALT,  0x0003E080       @ offset of buffer 1
    .equ DMA_END,  0x0276C5E0       @ DMA_BUF + 2 * DMA_ALT
    .equ ROW_BYTES, 7952            @ 3976 samples
    .equ ROWS,     2647             @ incl. the trailing row
    .equ SPAN,     0x800            @ bytes after dest that may be written (cache maintenance)
    .equ TABLES,   0x00028AB8       @ in ldimg, after its stub

.macro call_copy                     @ bl .Lcopy, without the relocation clang emits for bl
    mov     lr, pc                  @ pc reads as this + 8 = the instruction after the b
    b       .Lcopy
.endm

writejpgipp_stub:                   @ 0x26D98
    mov     r0, #0
    bx      lr

meta_hook:                          @ 0x26DA0
    push    {r4-r11, lr}
    ldr     r8, =TABLES
    add     ip, r4, #0x4600
    ldr     r5, [ip, #0x34]         @ job = [ctx+0x4634]
    ldrb    r1, [r5, #0x21A]        @ .BIA dark frame: leave it alone
    cmp     r1, #0
    bne     done
    add     ip, r4, #0x4200
    ldr     r1, [ip, #0x28]         @ row bytes [ctx+0x4228] (doubled by the store routine)
    ldr     r2, [ip, #0x2C]         @ rows [ctx+0x422C]
    ldr     r10, [ip, #0x20]        @ block index [ctx+0x4220]
    ldr     r3, =ROW_BYTES
    cmp     r1, r3
    ldreq   r3, =ROWS
    cmpeq   r2, r3
    bne     done                    @ only the geometry the template describes
    ldr     r6, =DMA_BUF            @ this block's buffer, as 0x3C61C-0x3C640
    tst     r10, #1
    ldrne   r3, =DMA_ALT
    addne   r6, r6, r3
    add     r6, r6, r7              @ + last-block bytes - row bytes = trailing row
    sub     r6, r6, r1
    ldr     r3, =DMA_BUF
    cmp     r6, r3
    blo     done
    ldr     r3, =DMA_END - SPAN
    cmp     r6, r3
    bhi     done

dng:                                @ IFD template + per-shot slots
    ldr     r2, [r8]                @ template length
    ldr     r11, [r8, #4]           @ slot count
    add     r9, r8, #28             @ slots: u16 template offset, job offset, length, 0
    add     r1, r9, r11, lsl #3     @ template follows the slots
    mov     r0, r6
    call_copy
slot:
    subs    r11, r11, #1
    bmi     black
    ldrh    r0, [r9], #2
    ldrh    r1, [r9], #2
    ldrh    r2, [r9], #4
    add     r0, r6, r0
    add     r1, r5, r1
    call_copy
    b       slot

black:                              @ BlackLevel per ISO (FINDINGS "Black level per ISO")
    ldr     r0, [r8, #8]            @ template offset of the BlackLevel value; 0 = none
    cmp     r0, #0
    beq     wb
    add     r9, r8, #12             @ 4 x u16 ISO thresholds, then u16 base black
    ldrh    r1, [r5, #0x66]         @ ISO (S+0x62, camera-verified)
    ldrh    r2, [r9, #8]            @ base black (92)
    mov     r3, #4
1:  ldrh    ip, [r9], #2
    cmp     r1, ip
    movhi   r2, r2, lsl #1          @ above this threshold: one more doubling
    subs    r3, r3, #1
    bne     1b
    push    {r2}                    @ value on own stack; low halfword = the SHORT
    add     r0, r6, r0
    mov     r1, sp
    mov     r2, #2
    call_copy
    add     sp, sp, #4

wb:                                 @ AsShotNeutral = the camera's per-shot neutral, if it is one
    ldrh    r0, [r8, #24]           @ template offset of the AsShotNeutral value; 0 = none
    ldrh    r1, [r8, #26]           @ job offset of the neutral (S+0xCC: 3 rationals, 24 bytes)
    cmp     r0, #0
    beq     flush
    add     r1, r5, r1
    mov     ip, #0x4000             @ as 0x3B430 stores it: 16384/r, 16384/16384, 16384/b
    ldr     r2, [r1]
    ldr     r3, [r1, #8]
    cmp     r2, ip
    cmpeq   r3, ip
    ldreq   r2, [r1, #12]
    ldreq   r3, [r1, #16]
    cmpeq   r2, ip
    cmpeq   r3, ip
    bne     flush                   @ not fetched (job default 7000/10000 ...): daylight neutral stays
    ldr     r2, [r1, #4]
    ldr     r3, [r1, #20]
    cmp     r2, #0
    cmpne   r3, #0
    beq     flush                   @ a zero denominator: daylight neutral stays
    add     r0, r6, r0
    mov     r2, #24
    call_copy

flush:                              @ written data to memory, no stale lines left behind
    bic     r0, r6, #31
    add     r1, r6, #SPAN
1:  mcr     p15, 0, r0, c7, c10, 1  @ clean D-cache line
    mcr     p15, 0, r0, c7, c6, 1   @ invalidate D-cache line
    add     r0, r0, #32
    cmp     r0, r1
    blo     1b
    mov     r0, #0
    mcr     p15, 0, r0, c7, c10, 4  @ drain write buffer

done:
    pop     {r4-r11, lr}
    sub     pc, lr, #0xC0           @ lr = 0x3C6AC -> continue at 0x3C5EC

@ .Lcopy: copy r2 bytes from r1 to r0 -- only if [r0, r0 + r2) lies inside the trailing row
@ [r6, r6 + ROW_BYTES) and inside the DMA buffer; otherwise nothing. Clobbers r0-r3, ip.
.Lcopy:
    cmp     r0, r6
    bxlo    lr
    add     ip, r0, r2
    cmp     ip, r0
    bxlo    lr                      @ length wrapped
    ldr     r3, =ROW_BYTES
    add     r3, r6, r3
    cmp     ip, r3
    bxhi    lr
    ldr     r3, =DMA_END
    cmp     ip, r3
    bxhi    lr
1:  subs    r2, r2, #1
    ldrbpl  r3, [r1], #1
    strbpl  r3, [r0], #1
    bpl     1b
    bx      lr

    .ltorg

@ raw_fetch (0x26F98): from the store routine 0x3CA58 at 0x3CB98 (stock `bne 0x3CC70`, flags of
@ `cmp type, #0`; type [ctx+0x4638]: 0 RAW/.BIA, 1 JPG, 2 DNG). JPG / DNG: as stock. A RAW file
@ (not .BIA) takes the same path as a DNG: 0x3CC70 calls 0x3B430(job, 0), which gets the review
@ image and the per-shot neutral from the Blackfin into job+0xD0 before the file's data is sent
@ (stock DNG+JPG order; the JPG then reuses it). The RAW path never reads its result. .BIA: stock.
@ Registers as stock: r0 = 0 kept, r1 is redefined on both paths; no stores.
    .equ FETCH,      0x0003CC70     @ mov r1, r0; ldr r0, [r7, r6]; bl 0x3B430; ...; b 0x3CB9C
    .equ NO_FETCH,   0x0003CB9C     @ stock fall-through: start of the file transfer

    .org 0x200
raw_fetch:
    ldrne   pc, =FETCH              @ JPG / DNG file: stock
    ldr     r1, [r7, r6]            @ job = [ctx+0x4634] (r6 = 0x4634 here)
    ldrb    r1, [r1, #0x21A]        @ .BIA dark frame?
    cmp     r1, #0
    ldreq   pc, =FETCH              @ RAW: fetch the neutral first, as for a DNG
    ldr     pc, =NO_FETCH           @ .BIA: stock

    .ltorg
