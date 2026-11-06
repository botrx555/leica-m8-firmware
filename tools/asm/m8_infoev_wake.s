@ Dial EV, "wake" version (2026-11-06, owner's request): the first dial click only opens the
@ exposure-compensation page; clicks while it is open change the value (as before). Same code and
@ layout as m8_infoev.s (entries, method table, strings at the same addresses), only the step call
@ in ev_key replaced by three nops.
    .set    WAKE, 1
    .include "m8_infoev.s"
