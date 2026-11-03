@ Exposure compensation on the rear dial (stage 15+): in shooting mode each dial click changes the
@ exposure compensation by 1/3 EV with Leica's own dial routine (0x65954, the unused `evfastaccess`
@ window's step: same setting, range 0..18 = -3..+3 EV, same "settings changed" event 0x2715) and
@ shows the value on the rear screen. The page closes 2 s after the last click (the dialog's own
@ timer), or on any other key (SET, INFO, half-press, ...); the value stays set.
@ Code placed over the `setting` debug command (0x2C44C-0x2C9F4, a printf of the settings table);
@ its entry stays a stub returning 0. Registration untouched.
@
@ 0x2C44C  `setting` entry, stub returning 0.
@ ev_held  from the same handler's "`a` held" exit (0x44E9C `bne 0x44F8C` -> `bne ev_held`; stock
@          ignores every key while `a` is held): dial keys only, not with the shutter fully pressed
@          (app+0x2548D) and not when the body has enabled Leica's own dial window (app+0x25744
@          bit 0, 0x45040); then as ev_key.
@ ev_key   from the application key handler's switch default (0x44EDC `b 0x44F8C` -> `b ev_key`),
@          r4 = GUI application, r5 = key: only `c` / `w` (dial) outside service mode, with
@          app+0x254B0 == 0 (as the INFO case) and not in Simple mode (override profile 5); step, then open BodyDebugDataDialog the camera's own
@          way (as stage 8's info_open) pointed at vt_ev, then arm its timer (0x8BD10, event 0x271E,
@          2000 ms, one shot; Leica arms the same timer with 100 ms). Returns through 0x44F8C.
@ on_key   dial: step, re-arm the timer, redraw. Any other key: close (the dialog's own method
@          table back, then its own SET path 0x61868: stops the timer, hides, closes camInfoMenu).
@ ev_timer GUI event 0x271E (handler word 0xEF2B4: 0x43B64 -> ev_timer): if the dialog is ours,
@          close it as above (already hidden: only restore the method table); otherwise go on to
@          Leica's handler 0x43B64 unchanged (service-mode Body Debug Data).
@ Painting: dialog base paint 0x605AC clears the window and calls our +0x48 (title) and +0x4C
@ (content); fill 0x98A40, text 0x996EC, text width 0x99C0C, sprintf 0xB94EC. The value text is
@ Leica's own (table 0xDA11C, as the playback info page 0x4576C).
@ Reads: the settings block [0xD4230] (active profile's exposure compensation, +0x1C+0x22).
@ Writes: the two flag bytes of stage 6, dialog+0 (method table), the dialog's timer (by 0x8BD10),
@ the exposure-compensation setting and the working-profile index (by Leica's 0x65954, as the SET
@ menu), the screen, our stack. No flash, EEPROM or card writes; no body commands.

    .syntax unified
    .arm
    .text

    .equ BASE,       0x0002C44C     @ load address of `stub` (for the method table)
    .equ MENU,       0x6F6C         @ camInfoMenu inside the application object
    .equ DIALOG,     0x138          @ BodyDebugDataDialog inside camInfoMenu
    .equ APP_DLG,    0x70A4         @ = MENU + DIALOG
    .equ DLG_TIMER,  0x60           @ the dialog's timer object
    .equ DLG_VT,     0x000ED1B8     @ the dialog's own method table
    .equ DLG_KEY,    0x00061868     @ the dialog's own key method (SET = close)
    .equ SHOW_PAGE,  0x00058FE8
    .equ SHOW,       0x00040360
    .equ GATE,       0x000403F0     @ (app+0x254A0) -> 0 = service mode
    .equ RETURN,     0x00044F8C     @ application key handler epilogue
    .equ EV_STEP,    0x00065954     @ (-, key): `w` +1/3, `c` -1/3 EV (evfastaccess)
    .equ ARM,        0x0008BD10     @ (timer, event, ms, one shot, [sp] 0, 0, 0)
    .equ TIMER_EV,   0x271E
    .equ TIMER_MS,   2000
    .equ TIMER_ORIG, 0x00043B64     @ Leica's handler of event 0x271E
    .equ P_SETTINGS, 0x000D4230     @ -> settings block
    .equ EV_TEXT,    0x000DA11C     @ 19 x {key, string}: "-3" .. "0" .. "+3"
    .equ FILL,       0x00098A40     @ (x, y, w, h, [sp] colour565)
    .equ TEXT,       0x000996EC     @ (x, y, w, h, [sp] str, [sp+4] colour565, [sp+8] font)
    .equ TWIDTH,     0x00099C0C     @ (str, font) -> width + 1
    .equ SPRINTF,    0x000B94EC
    .equ F3,         0x30000        @ font 3 (FONT1, 17 px) << 16
    .equ F4,         0x40000        @ font 4 (FONT2, 21 px) << 16
    .equ WHITE,      0xFFFF
    .equ GREY,       0x94B2         @ (150, 150, 150)
    .equ FOOT,       0x6B6D         @ (110, 110, 110)
    .equ BAR,        0x2945         @ (40, 40, 40)
    .equ AMBER,      0xFD20
    .equ X0,         34             @ scale: tick i at X0 + i * DX, i = 0..18
    .equ DX,         14
    .equ YS,         140

.macro fcall addr                   @ call a firmware routine without a relocation
    mov     lr, pc                  @ pc reads as this + 8 = the instruction after the ldr
    ldr     pc, =\addr
.endm
.macro la reg, label                 @ absolute address of a label here (adr cannot reach)
    ldr     \reg, =(BASE + \label - stub)
.endm
.macro lcall label                  @ call a local routine
    mov     lr, pc
    b       \label
.endm

stub:
    mov     r0, #0
    bx      lr

ev_held:                            @ from 0x44E9C: `a` held, where the handler ignores every key
    cmp     r5, #'c'
    cmpne   r5, #'w'
    ldrne   pc, =RETURN             @ not the dial: as before (registers untouched)
    add     r3, r4, #0x25400
    ldrb    r2, [r3, #0x8D]
    cmp     r2, #0
    ldrne   pc, =RETURN             @ shutter fully pressed (`S` held): stock
    ldr     r2, [r3, #0x344]
    tst     r2, #1
    ldrne   pc, =RETURN             @ the body enabled Leica's own dial window (0x45040): stock
    b       1f

ev_key:                             @ r4 = GUI application, r5 = key
    cmp     r5, #'c'
    cmpne   r5, #'w'
    ldrne   pc, =RETURN             @ not the dial: as before (registers untouched)
1:  push    {r0, r1}                @ r0 = the handler's return value so far: kept as stock
    add     r3, r4, #0x25400        @ (0x44E30 saves only r4, r5: use r0-r3, ip)
    ldrb    r2, [r3, #0xB0]
    cmp     r2, #0
    bne     9f                      @ app+0x254B0 set: ignored, as the INFO case
    add     r0, r3, #0xA0
    fcall   GATE
    cmp     r0, #0
    beq     9f                      @ service mode: stock
    ldr     r0, =P_SETTINGS
    ldr     r0, [r0]
    ldrh    r0, [r0, #2]
    cmp     r0, #5
    beq     9f                      @ Simple mode (override profile 5): stock
    mov     r1, r5
    fcall   EV_STEP
    add     r0, r4, #(MENU & 0xFF00)
    add     r0, r0, #(MENU & 0x00FF)
    mov     r1, #1
    fcall   SHOW_PAGE               @ show camInfoMenu, as 0x44F78
    add     r3, r4, #0x25400
    mov     r2, #1
    strb    r2, [r3, #0x8F]         @ app+0x2548F = 1, as 0x44F88
    add     r0, r4, #(MENU & 0xFF00)
    add     r0, r0, #(MENU & 0x00FF)
    mov     r3, #0
    strb    r3, [r0, #0x99]         @ menu+0x99 = 0, as 0x65630
    add     r0, r0, #DIALOG
    la      r1, vt_ev
    str     r1, [r0]                @ the dialog paints and handles keys with our methods
    mov     r1, #1
    fcall   SHOW                    @ show it, as 0x6567C
    add     r0, r4, #(APP_DLG & 0xFF00)
    add     r0, r0, #(APP_DLG & 0x00FF)
    lcall   arm
9:  pop     {r0, r1}
    ldr     pc, =RETURN

arm:                                @ (dialog): its timer -> event 0x271E in 2 s, one shot
    push    {lr}
    sub     sp, sp, #12
    mov     r3, #0
    str     r3, [sp]
    str     r3, [sp, #4]
    str     r3, [sp, #8]
    add     r0, r0, #DLG_TIMER
    ldr     r1, =TIMER_EV
    mov     r2, #TIMER_MS
    mov     r3, #1
    fcall   ARM                     @ as 0x61180 (100 ms there)
    add     sp, sp, #12
    pop     {pc}

on_key:                             @ (this, key)
    and     r1, r1, #0xFF
    cmp     r1, #'c'
    cmpne   r1, #'w'
    bne     close                   @ any other key: close
    push    {r4, lr}
    mov     r4, r0
    fcall   EV_STEP
    mov     r0, r4
    lcall   arm
    mov     r0, r4
    pop     {r4, lr}
    ldr     r2, [r0]
    mov     r1, #'u'
    ldr     pc, [r2, #0x1C]         @ redraw (method +0x1C, as stages 7 and 13), r0 = this
close:                              @ (dialog): with the dialog's own methods
    ldr     r2, =DLG_VT
    str     r2, [r0]
    mov     r1, #'s'
    ldr     pc, =DLG_KEY

ev_timer:                           @ (app, event): GUI event 0x271E
    add     ip, r0, #(APP_DLG & 0xFF00)
    add     ip, ip, #(APP_DLG & 0x00FF)
    ldr     r3, [ip]
    la      r2, vt_ev
    cmp     r3, r2
    ldrne   pc, =TIMER_ORIG         @ not ours: Leica's handler (r0, r1 unchanged)
    push    {lr}
    mov     r0, ip
    ldr     r3, [r0, #0x20]
    tst     r3, #4                  @ already hidden: only give the methods back
    ldrne   r2, =DLG_VT
    strne   r2, [r0]
    beq     1f
    mov     r0, #0
    pop     {pc}
1:  lcall   close
    mov     r0, #0
    pop     {pc}

@ ---- helpers
text:                               @ (x, y, str, colour | font << 16), box to the right edge
    push    {r4, lr}
    sub     sp, sp, #16
    str     r2, [sp]
    mov     r4, r3, lsr #16
    str     r4, [sp, #8]
    mov     r3, r3, lsl #16
    mov     r3, r3, lsr #16
    str     r3, [sp, #4]
    rsb     r2, r0, #320
    mov     r3, #30
    fcall   TEXT
    add     sp, sp, #16
    pop     {r4, pc}

fill:                               @ (x, y, w, h), ip = colour
    push    {lr}
    sub     sp, sp, #4
    str     ip, [sp]
    fcall   FILL
    add     sp, sp, #4
    pop     {pc}

@ ---- title bar (+0x48)
title:                              @ (this)
    push    {lr}
    ldr     ip, =BAR
    mov     r0, #0
    mov     r1, #0
    mov     r2, #320
    mov     r3, #25
    lcall   fill
    mov     r0, #8
    mov     r1, #4
    la      r2, s_title
    ldr     r3, =(WHITE | F3)
    lcall   text
    mov     r0, #8
    mov     r1, #218
    la      r2, s_foot
    ldr     r3, =(FOOT | F3)
    lcall   text
    pop     {pc}

@ ---- content (+0x4C): value and scale
content:                            @ (this)
    push    {r4-r7, lr}
    sub     sp, sp, #36
    ldr     r0, =P_SETTINGS
    ldr     r0, [r0]
    ldrh    r1, [r0, #2]            @ override profile, 0xFFFF = use [0] (as 0x65954)
    ldr     r2, =0xFFFF
    cmp     r1, r2
    ldrheq  r1, [r0]
    cmp     r1, #5
    bhi     9f                      @ no such profile: draw nothing
    mov     r2, #0x50
    mla     r0, r1, r2, r0
    ldrsh   r4, [r0, #(0x1C + 0x22)]
    cmp     r4, #18
    bhi     9f                      @ out of range (unsigned: < 0 too): draw nothing
    ldr     r1, =EV_TEXT + 4
    ldr     r2, [r1, r4, lsl #3]    @ Leica's text for this step
    la      r1, s_fmt
    mov     r0, sp
    fcall   SPRINTF                 @ "-2/3 EV"
    mov     r0, sp
    mov     r1, #4
    fcall   TWIDTH
    rsb     r0, r0, #320
    mov     r0, r0, lsr #1          @ centred
    mov     r1, #78
    mov     r2, sp
    ldr     r3, =(WHITE | F4)
    cmp     r4, #9
    ldrne   r3, =(AMBER | F4)       @ not 0: amber
    lcall   text
    mov     r5, #0                  @ scale: 19 ticks, full stops taller
    mov     r7, #X0
1:  mov     r0, r5
2:  subs    r0, r0, #3
    bpl     2b
    cmn     r0, #3                  @ r5 % 3 == 0
    mov     r0, r7
    moveq   r1, #(YS + 4)
    movne   r1, #(YS + 10)
    moveq   r3, #14
    movne   r3, #6
    mov     r2, #2
    ldr     ip, =GREY
    lcall   fill
    add     r5, r5, #1
    add     r7, r7, #DX
    cmp     r5, #19
    blo     1b
    mov     r0, #DX                 @ marker on the current step
    mul     r0, r4, r0
    add     r0, r0, #(X0 - 3)
    mov     r1, #YS
    mov     r2, #8
    mov     r3, #22
    ldr     ip, =WHITE
    cmp     r4, #9
    ldrne   ip, =AMBER
    lcall   fill
    mov     r0, #(X0 - 6)           @ -3, 0, +3 under the scale
    mov     r1, #(YS + 26)
    la      r2, s_m3
    ldr     r3, =(GREY | F3)
    lcall   text
    mov     r0, #(X0 + 9 * DX - 3)
    mov     r1, #(YS + 26)
    la      r2, s_0
    ldr     r3, =(GREY | F3)
    lcall   text
    mov     r0, #(X0 + 18 * DX - 10)
    mov     r1, #(YS + 26)
    la      r2, s_p3
    ldr     r3, =(GREY | F3)
    lcall   text
9:  add     sp, sp, #36
    pop     {r4-r7, pc}

    .align  2
    .ltorg

    .align  2
vt_ev:                              @ copy of 0xED1B8 with keys / title / content replaced
    .word   0xCD34C, 0xCD354, 0xCD35C, 0xCD364, 0x9A8D0, 0xD1E6C, 0xD1EB4, 0x9A47C
    .word   0xCD36C, 0x605AC, 0x9A8F0, BASE + (on_key - stub), 0x9ACD8, 0x9AD68, 0x9AD6C, 0x40360
    .word   0xCD370, 0xCD374, BASE + (title - stub), BASE + (content - stub), 0, 0

s_title:  .asciz "Exposure compensation"
s_foot:   .asciz "Dial: adjust    SET: done"
s_fmt:    .asciz "%s EV"
s_m3:     .asciz "-3"
s_0:      .asciz "0"
s_p3:     .asciz "+3"
    .align  2
@ size checked by the patch script (region 0x2C44C-0x2C9F4, 1448 bytes)
