// Minimal Android (bionic, arm64) executable that runs CPython from libpython.
// Packaged as lib/arm64-v8a/libmosaicpy.so so Android installs it into the app's
// native-library directory, the only app location it may execute from.
//
// The bionic dynamic linker loads libpython and libc and runs libc's own
// initialisers (environ, TLS, stdio) before jumping here, so _start only has
// to pass argc/argv to Py_BytesMain and exit with its status.
        .text
        .globl  _start
        .type   _start, %function
_start:
        mov     x29, #0
        mov     x30, #0
        ldr     x0, [sp]            // argc
        add     x1, sp, #8          // argv
        bl      Py_BytesMain
        bl      exit                // flushes stdio, runs atexit handlers
        brk     #0
        .size   _start, . - _start
        .section .note.GNU-stack, "", %progbits
