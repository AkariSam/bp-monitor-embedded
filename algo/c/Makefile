CC     = gcc
CFLAGS = -O2 -Wall -Wextra
TARGET = build/coverify
SRCS   = main_pc.c bp_dsp.c

$(TARGET): $(SRCS)
	mkdir -p build
	$(CC) $(CFLAGS) -o $@ $(SRCS) -lm

.PHONY: clean
clean:
	rm -rf build
