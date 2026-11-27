#if EV_F2001_AUDIO
.section .rodata.f2001,"a"
.balign 4
.global f2001_samples
f2001_samples:
.incbin "src/f2001.pcm"
#endif
