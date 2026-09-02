# =============================================================================
# RPC framework - build a native core, with the command chosen from the host OS.
#
#   make core1        CORE1 = native C build   (desktop/loopback test build)
#   make core2        CORE2 = native C server  (flags picked from host OS)
#   make core3        CORE3 = native C client  (flags picked from host OS)
#   make all          build all three native cores for this host
#   make clean        remove the built native binaries
#
# CORE1 normally ships as the Android app - build that in Android Studio
# (Gradle/NDK) from src/android_projects/aidlserver.zip. The `make core1`
# target below is a native desktop build of the same sources, handy for
# running all three cores together on one machine over loopback (127.0.0.1).
#
# NOTE: the RPC stubs (rpc_fncode.h, rpc_core*_client.c / _server.c and the
# AIDL) are generated separately with:   python src/python_scripts/generator_code.py
# Generate them before building - this Makefile only compiles and links.
#
# CORE2 (server, typically Windows) and CORE3 (client, typically a Linux VM)
# are ordinary C programs: on Windows they link Winsock (-lws2_32) and get a
# .exe suffix; on Linux/macOS they link pthreads.
#
# The OS is detected from the machine running `make` (not from the core), so
# build each native core on the box it will run on - e.g. `make core3` on the
# Linux VM, `make core2` on Windows.
# =============================================================================

# Use := (not ?=) so make's built-in default CC=cc doesn't win; a command-line
# override such as `make CC=clang core2` still takes precedence over this.
CC      := gcc

# Project layout: headers live in inc/, C sources in src/rpc_src/.
# Keep the include path in its own var so a command-line CFLAGS override
# (which would replace CFLAGS entirely) can't drop it.
INCDIR   := inc
SRCDIR   := src/rpc_src
CFLAGS   ?=
INCLUDES := -I$(INCDIR)

# Framework sources shared by every native core.
COMMON  := $(SRCDIR)/main_file.c $(SRCDIR)/osal.c $(SRCDIR)/rpc_marshall.c

# ---- host-OS specific settings ---------------------------------------------
ifeq ($(OS),Windows_NT)
    HOST    := Windows
    EXE     := .exe
    LDLIBS  := -lws2_32
    RM_F    := cmd /c del /q
else
    HOST    := $(shell uname -s)
    EXE     :=
    LDLIBS  := -pthread
    RM_F    := rm -f
endif

.DEFAULT_GOAL := help
.PHONY: help all core1 core2 core3 clean

help:
	@echo "RPC build - targets:"
	@echo "  make core1      CORE1 native test build  (host: $(HOST))"
	@echo "  make core2      CORE2 native build  (host: $(HOST))"
	@echo "  make core3      CORE3 native build  (host: $(HOST))"
	@echo "  make all        core1 + core2 + core3"
	@echo "  make clean      remove built binaries"
	@echo "  (generate stubs first with: python src/python_scripts/generator_code.py)"
	@echo "  (CORE1 normally ships as the Android app - build that in Android Studio)"

all: core1 core2 core3

# ---- CORE1 / CORE2 / CORE3 : native C builds -------------------------------
core1:
	@echo "Building CORE1 for $(HOST) (LDLIBS=$(LDLIBS))..."
	$(CC) $(CFLAGS) $(INCLUDES) $(COMMON) $(SRCDIR)/rpc_core1.c $(SRCDIR)/rpc_core1_client.c $(SRCDIR)/rpc_core1_server.c -o core1$(EXE) $(LDLIBS)

core2:
	@echo "Building CORE2 for $(HOST) (LDLIBS=$(LDLIBS))..."
	$(CC) $(CFLAGS) $(INCLUDES) $(COMMON) $(SRCDIR)/rpc_core2.c $(SRCDIR)/rpc_core2_client.c $(SRCDIR)/rpc_core2_server.c -o core2$(EXE) $(LDLIBS)

core3:
	@echo "Building CORE3 for $(HOST) (LDLIBS=$(LDLIBS))..."
	$(CC) $(CFLAGS) $(INCLUDES) $(COMMON) $(SRCDIR)/rpc_core3.c $(SRCDIR)/rpc_core3_client.c $(SRCDIR)/rpc_core3_server.c -o core3$(EXE) $(LDLIBS)

clean:
	-$(RM_F) core1$(EXE) core2$(EXE) core3$(EXE)
