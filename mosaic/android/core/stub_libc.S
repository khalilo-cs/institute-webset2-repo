// Link-time stub only: gives the launcher a DT_NEEDED on libc.so and an
// `exit` symbol to link against. The device's real libc.so is used at runtime.
        .text
        .globl  exit
        .type   exit, %function
exit:
        ret
        .size   exit, . - exit
