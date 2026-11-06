@ Exposure compensation and ISO on the rear dial (`--dial-ev --iso`, 2026-11-06, owner's design).
@ As m8_infoev_wake.s (first dial click only opens the EV page, clicks while it is open step, closes
@ 2 s after the last key or on any other key), plus a second page, ISO: UP / DOWN (`u` / `d`) switch
@ between the EV and ISO pages while one is open; the dial changes the shown page's value.
@ The page shown = the method table the dialog points at (vt_ev / vt_iso): no state memory.
@ ISO page: AUTO + Leica's menu texts (table 0xEF63C: [AUTO ISO], 160, 320, 640, 1250, 2500) in two rows
@ of three, the current one highlighted; the dial moves in that order, clamped at both ends.
@ ISO step = the SET menu's ISO commit (0x4E4D4) in the order of Leica's EV dial step 0x65954: read
@ the active profile's ISO (+0x20: 0xFFFF AUTO, 0..4 = 160..2500), switch to working profile 4
@ (0x219D4(settings, 4)), store the new value in it, post "settings changed" 0x2715 with the menu's
@ routine 0x35208([0xD421C], [0xD4220]); the main task sends ISO to the body (0x3525C).
@ Code over four adjacent reclaimed debug commands: `bf_dmatest` 0x2C038, `mgs` 0x2C220, `stress`
@ 0x2C2F4 and `setting` 0x2C44C-0x2C9F4 (only references: their debug-shell registrations); the four
@ entries stay stubs returning 0 (pinned by .org). Registrations untouched.
@
@ Hooks (as m8_infoev.s, new addresses): 0x44EDC `b ev_key`, 0x44E9C `bne ev_held`, 0xEF2B4 ev_timer.
@ Reads: the settings block [0xD4230]. Writes: the two flag bytes of stage 6, dialog+0 (method
@ table), the dialog's timer (by 0x8BD10), exposure compensation + working-profile index (by Leica's
@ 0x65954), ISO (profile +0x20) + working-profile index (by 0x219D4), the screen, our stack.
@ No flash, EEPROM or card writes; no body commands (the main task sends the settings, as for the menu).

    .syntax unified
    .arm
    .text

    .equ BASE,       0x0002C038     @ load address of `start` (the `bf_dmatest` entry)
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
    .equ SETPROF,    0x000219D4     @ (settings, 4): working profile 4, as the SET menu
    .equ CHANGED,    0x00035208     @ ([0xD421C], [0xD4220]): post 0x2715, as the SET menu
    .equ P_CTX,      0x000D421C
    .equ P_APP,      0x000D4220
    .equ ARM,        0x0008BD10     @ (timer, event, ms, one shot, [sp] 0, 0, 0)
    .equ TIMER_EV,   0x271E
    .equ TIMER_MS,   2000
    .equ TIMER_ORIG, 0x00043B64     @ Leica's handler of event 0x271E
    .equ P_SETTINGS, 0x000D4230     @ -> settings block
    .equ EV_TEXT,    0x000DA11C     @ 19 x {key, string}: "-3" .. "0" .. "+3"
    .equ ISO_TEXT,   0x000EF63C     @ 6 x string: "AUTO ISO", "160" .. "2500" (SET menu)
    .equ FILL,       0x00098A40     @ (x, y, w, h, [sp] colour565)
    .equ TEXT,       0x000996EC     @ (x, y, w, h, [sp] str, [sp+4] colour565, [sp+8] font)
    .equ TWIDTH,     0x00099C0C     @ (str, font) -> width + 1
    .equ SPRINTF,    0x000B94EC
    .equ F3,         0x30000        @ font 3 (FONT1, 17 px) << 16
    .equ F4,         0x40000        @ font 4 (FONT2, 21 px) << 16
    .equ WHITE,      0xFFFF
    .equ BLACK,      0x0000
    .equ GREY,       0x94B2         @ (150, 150, 150)
    .equ FOOT,       0x6B6D         @ (110, 110, 110)
    .equ BAR,        0x2945         @ (40, 40, 40)
    .equ AMBER,      0xFD20
    .equ X0,         34             @ EV scale: tick i at X0 + i * DX, i = 0..18
    .equ DX,         14
    .equ YS,         140
    .equ CX,         14             @ ISO grid: cell i at CX + (i % 3) * CDX, CY + (i / 3) * CDY
    .equ CDX,        102
    .equ CW,         92
    .equ CY,         52
    .equ CDY,        76
    .equ CH,         64

.macro fcall addr                   @ call a firmware routine without a relocation
    mov     lr, pc                  @ pc reads as this + 8 = the instruction after the ldr
    ldr     pc, =\addr
.endm
.macro la reg, label                 @ absolute address of a label here (adr cannot reach)
    ldr     \reg, =(BASE + \label - start)
.endm
.macro lcall label                  @ call a local routine
    mov     lr, pc
    b       \label
.endm

start:                              @ `bf_dmatest` entry (0x2C038): stub
    mov     r0, #0
    bx      lr

profile:                            @ -> r0 = active profile (settings + 0x1C + 0x50 * index) or 0
    ldr     r1, =P_SETTINGS
    ldr     r1, [r1]
    ldrh    r2, [r1, #2]            @ override profile, 0xFFFF = use [0] (as 0x65954)
    ldr     r3, =0xFFFF
    cmp     r2, r3
    ldrheq  r2, [r1]
    cmp     r2, #5
    movhi   r0, #0
    bxhi    lr
    mov     r3, #0x50
    mla     r0, r2, r3, r1
    add     r0, r0, #0x1C
    bx      lr

iso_pos:                            @ -> r0 = position 0..5 of the active profile's ISO, > 5 = none
    push    {lr}
    lcall   profile
    cmp     r0, #0
    ldrhne  r0, [r0, #0x20]         @ 0xFFFF AUTO, 0..4 = 160..2500
    moveq   r0, #0xFF
    add     r0, r0, #1
    mov     r0, r0, lsl #16
    mov     r0, r0, lsr #16         @ AUTO -> 0, 160 -> 1 .. 2500 -> 5
    pop     {pc}

iso_step:                           @ (key): `w` up, `c` down, clamped to AUTO .. 2500
    push    {r4, lr}
    mov     r4, r0
    lcall   iso_pos
    cmp     r0, #5
    bhi     9f                      @ unknown value: leave it alone
    cmp     r4, #'w'
    addeq   r0, r0, #1
    cmp     r4, #'c'
    subeq   r0, r0, #1
    cmp     r0, #0
    movlt   r0, #0
    cmp     r0, #5
    movgt   r0, #5
    sub     r4, r0, #1              @ new value: -1 (stored as 0xFFFF) = AUTO, 0..4
    ldr     r0, =P_SETTINGS
    ldr     r0, [r0]
    mov     r1, #4
    fcall   SETPROF                 @ working profile 4, as the SET menu and 0x65954
    lcall   profile
    cmp     r0, #0
    strhne  r4, [r0, #0x20]
    ldr     r0, =P_CTX
    ldr     r0, [r0]
    ldr     r1, =P_APP
    ldr     r1, [r1]
    fcall   CHANGED                 @ settings changed (0x2715), as the SET menu
9:  pop     {r4, pc}

@ ---- ISO content (+0x4C of vt_iso): two rows of three, the current value highlighted
content_iso:                        @ (this)
    push    {r4-r8, lr}
    lcall   iso_pos
    mov     r8, r0
    mov     r4, #0
1:  cmp     r4, #3
    subhs   r0, r4, #3
    movlo   r0, r4
    mov     r1, #CDX
    mul     r5, r0, r1
    add     r5, r5, #CX             @ x
    movlo   r6, #CY
    movhs   r6, #(CY + CDY)         @ y
    mov     r0, r5
    mov     r1, r6
    mov     r2, #CW
    mov     r3, #CH
    ldr     ip, =BAR
    cmp     r4, r8
    ldreq   ip, =AMBER
    lcall   fill
    ldr     r7, =ISO_TEXT
    ldr     r7, [r7, r4, lsl #2]    @ Leica's menu text
    cmp     r4, #0
    ldreq   r7, =(BASE + s_auto - start)   @ "AUTO" ("AUTO ISO" is wider than a cell)
    mov     r0, r7
    mov     r1, #4
    fcall   TWIDTH
    rsb     r0, r0, #CW
    add     r0, r5, r0, asr #1      @ centred in the cell
    add     r1, r6, #20
    mov     r2, r7
    ldr     r3, =(WHITE | F4)
    cmp     r4, r8
    ldreq   r3, =(BLACK | F4)
    lcall   text
    add     r4, r4, #1
    cmp     r4, #6
    blo     1b
    pop     {r4-r8, pc}

    .align  2
    .ltorg

    .org    0x1E8                   @ = 0x2C220, the `mgs` entry
stub_mgs:
    mov     r0, #0
    bx      lr

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

    .align  2
    .ltorg

    .org    0x2BC                   @ = 0x2C2F4, the `stress` entry
stub_stress:
    mov     r0, #0
    bx      lr

@ ---- title bar (+0x48), both pages
title:                              @ (this)
    push    {r4, lr}
    ldr     r4, [r0]
    ldr     ip, =BAR
    mov     r0, #0
    mov     r1, #0
    mov     r2, #320
    mov     r3, #25
    lcall   fill
    mov     r0, #8
    mov     r1, #4
    la      r2, s_title
    la      r3, vt_iso
    cmp     r4, r3
    ldreq   r2, =(BASE + s_iso - start)   @ ISO page
    ldr     r3, =(WHITE | F3)
    lcall   text
    mov     r0, #8
    mov     r1, #218
    la      r2, s_foot
    ldr     r3, =(FOOT | F3)
    lcall   text
    pop     {r4, pc}

    .align  2
    .ltorg

    .org    0x414                   @ = 0x2C44C, the `setting` entry
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
                                    @ first click: only open the EV page (no step)
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
    cmp     r1, #'u'
    cmpne   r1, #'d'
    beq     swap                    @ UP / DOWN: the other page
    cmp     r1, #'c'
    cmpne   r1, #'w'
    bne     close                   @ any other key: close
    push    {r4, lr}
    mov     r4, r0
    ldr     r2, [r0]
    la      r3, vt_iso
    cmp     r2, r3
    beq     1f
    fcall   EV_STEP                 @ EV page: Leica's step (r1 = key)
    b       2f
1:  mov     r0, r1
    lcall   iso_step                @ ISO page
2:  mov     r0, r4
    lcall   arm
    mov     r0, r4
    pop     {r4, lr}
redraw:
    ldr     r2, [r0]
    mov     r1, #'u'
    ldr     pc, [r2, #0x1C]         @ redraw (method +0x1C, as stages 7 and 13), r0 = this
swap:
    ldr     r2, [r0]
    la      r1, vt_ev
    la      r3, vt_iso
    cmp     r2, r3
    moveq   r3, r1                  @ ISO -> EV, EV -> ISO
    str     r3, [r0]
    push    {r0, lr}
    lcall   arm
    pop     {r0, lr}
    b       redraw
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
    beq     1f
    la      r2, vt_iso
    cmp     r3, r2
    ldrne   pc, =TIMER_ORIG         @ not ours: Leica's handler (r0, r1 unchanged)
1:  push    {lr}
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

@ ---- EV content (+0x4C of vt_ev): value and scale (as m8_infoev.s)
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
    .word   0xCD36C, 0x605AC, 0x9A8F0, BASE + (on_key - start), 0x9ACD8, 0x9AD68, 0x9AD6C, 0x40360
    .word   0xCD370, 0xCD374, BASE + (title - start), BASE + (content - start), 0, 0
vt_iso:                             @ the same with the ISO content
    .word   0xCD34C, 0xCD354, 0xCD35C, 0xCD364, 0x9A8D0, 0xD1E6C, 0xD1EB4, 0x9A47C
    .word   0xCD36C, 0x605AC, 0x9A8F0, BASE + (on_key - start), 0x9ACD8, 0x9AD68, 0x9AD6C, 0x40360
    .word   0xCD370, 0xCD374, BASE + (title - start), BASE + (content_iso - start), 0, 0

s_title:  .asciz "Exposure compensation"
s_iso:    .asciz "ISO"
s_foot:   .asciz "Dial: adjust  UP/DN: EV/ISO  SET: ok"
s_auto:   .asciz "AUTO"
s_fmt:    .asciz "%s EV"
s_m3:     .asciz "-3"
s_0:      .asciz "0"
s_p3:     .asciz "+3"
    .align  2
@ size checked by the patch script (region 0x2C038-0x2C9F4, 2492 bytes)
