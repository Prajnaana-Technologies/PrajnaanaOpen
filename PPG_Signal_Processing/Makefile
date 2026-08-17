# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
#
# SPDX-License-Identifier: Apache-2.0
#
# Original Author: Mamatha BV
#
# ===========================================================================
# PPG HR / HRV / RR / RRV tracker.
#
#   make            build ./ppg_analysis.  THIS IS THE NORMAL BUILD -- no
#                   argument is needed, and it is the only one most users want.
#
# The rest are for specific situations, not alternatives to the above:
#
#   make strict     same code, stricter compiler: -Werror -Wshadow.  Writes the
#                   SAME binary name, so it overwrites ./ppg_analysis.  Run it
#                   before proposing a change, as a gate.
#   make debug      -O0 -g -> ppg_analysis_debug, for stepping in a debugger
#   make asan       ASan + UBSan -> ppg_analysis_asan, for hunting memory or
#                   undefined-behaviour bugs.  Run with ASAN_OPTIONS=detect_leaks=0
#   make clean      remove built artefacts AND any run outputs left here
#   make help       print this list
#
# debug and asan use distinct binary names, so they coexist with the normal one.
#
# ONE BINARY SERVES EVERYTHING.  There are no per-patient and no per-detector
# builds; both are runtime selections:
#
#   ./ppg_analysis -i rec.txt -s neonate          patient type: neonate|child|adult
#   ./ppg_analysis -i rec.txt -s adult -d ims     override the detector, for A/B
#
# Run ./ppg_analysis with no arguments for the full option list, which is generated
# from the tables in ppg_main.c and ppg_fiducial.c and so cannot go stale.
#
# Sources are in src/, headers in include/, documentation in docs/ --
# USER_GUIDE.md is the operating manual, RESULTS.md every measured figure,
# ppg_arch.md the module map, DESIGN.md the reasoning behind each decision,
# FIDUCIAL_INTERFACE.md the detector contract.
# Run the program from a scratch directory, not from here -- it writes its CSVs
# into the current working directory:  mkdir -p run && cd run && ../ppg_analysis ...
# ===========================================================================

# ---------------------------------------------------------------------------
# The standard and warning flags are PINNED deliberately.
#
# -std=c99      the language this code targets.  Building without it hides real
#               problems: `strncasecmp` is POSIX rather than ISO C, and a lax
#               build silently accepted it as an implicit declaration -- which
#               assumes an int return and unprototyped arguments, and is exactly
#               the kind of problem that works on one machine and breaks on the
#               next.
# -Wall -Wextra -Wpedantic
#               the tree is clean at this level on gcc and clang.  Anything new
#               that trips them is a regression, not background noise.
#
# Do not relax these to make a change compile.  Fix the change.
# ---------------------------------------------------------------------------
CC      ?= cc
SRCDIR   = src
INCDIR   = include
CSTD     = -std=c99
WARN     = -Wall -Wextra -Wpedantic
OPT     ?= -O2
# Everything every target needs.  The per-target recipes below add only their
# own optimisation/sanitiser flags on top, so none can omit the include path.
BASEFLAGS = $(CSTD) $(WARN) -I$(INCDIR)
# The program's own name is a property of the BUILD -- it is whatever this file
# writes -- so each recipe below passes the name it is actually producing, and the
# binary reports that name in its banner and in every diagnostic.  Build it under
# another name and it says so, with nothing to keep in step by hand.
NAMEDEF   = -DPPG_PROG_NAME='"$(BIN)"'
CFLAGS   = $(BASEFLAGS) $(OPT)
LDLIBS   = -lm
BIN      = ppg_analysis

# ---------------------------------------------------------------------------
# Sources.
#
# BOTH detectors are always compiled and linked; ppg_fiducial.c dispatches
# between them at run time.  Each detector keeps all of its state and helpers
# file-scope and prefixed, so only the two entry points named in ppg_fiducial.h
# have external linkage and the two cannot collide.  That is also what an MSVC
# project or a CMake glob produces, so this build exercises the same path rather
# than a special case that only works here.
# ---------------------------------------------------------------------------
SRC      = $(SRCDIR)/ppg_main.c $(SRCDIR)/ppg_analysis.c $(SRCDIR)/ppg_fiducial.c \
           $(SRCDIR)/ppg_fiducial_elgendi_terma.c $(SRCDIR)/ppg_fiducial_karlen_ims.c \
           $(SRCDIR)/ppg_RR.c $(SRCDIR)/chebyshev_t2_o4.c
HDR      = $(INCDIR)/ppg_common.h $(INCDIR)/ppg_fiducial.h $(INCDIR)/filter_bands.h

# ---------------------------------------------------------------------------
# Compile-time switches.  There are two, and neither is needed for a normal
# build -- they exist so a behaviour can be measured with and without it.
#
#   -DRR_TRACK_HARMONIC_GUARD=0
#       Disable the sequential-tracking harmonic guard (Zhang et al 2025).
#       Default is 1.  With it off, a slow breather's spectrum may lock onto a
#       harmonic and report a multiple of the true rate.  Turning it off costs
#       accuracy, coverage and sub-harmonic rejection together; the measured A/B
#       is in docs/DESIGN.md, "Harmonic guard by sequential tracking".
#
#   -DSUBJECT_DEFAULT=<0|1|2>
#       Which row of g_subject[] applies when -s is omitted (0 neonate,
#       1 child, 2 adult).  Default 2.  This only moves the DEFAULT; every
#       category is always compiled in and reachable with -s.
#
# Those two are the only options intended for a build.  Several other
# constants are
# also #ifndef-guarded -- RR_MIN_PEAK_PROMINENCE, RR_INTP_GRID_HZ,
# PPG_SMOOTH_MS, the IMS_* amplitude ratios -- but a guard there exists so a
# value can be SWEPT during a measurement, not so a product can be built with a
# different one.  Each carries its citation at the point of definition; a shipped
# change goes in the header, with the source for the new value, and the full
# validation is re-run.
# ---------------------------------------------------------------------------

.PHONY: all strict asan debug clean help

# ---------------------------------------------------------------------------
# all -- THE DEFAULT.  `make` with no argument runs this and it is the only
# target most users ever need.  -O2, no debug symbols.  Nothing about
# the program's behaviour depends on which target below you build; they differ
# only in what the COMPILER does with the same source.
# ---------------------------------------------------------------------------
all: $(BIN)

$(BIN): $(SRC) $(HDR)
	$(CC) $(CFLAGS) $(NAMEDEF) -o $@ $(SRC) $(LDLIBS)

# ---------------------------------------------------------------------------
# strict -- the same code, a stricter compiler.  Adds -Werror (every warning
# becomes an error) and -Wshadow (a local hiding an outer name).
#
# NOTE IT WRITES $(BIN), THE SAME NAME AS `all`, and therefore overwrites it.
# That is deliberate: this is not a different product, it is the product built
# with the warnings turned into a gate.  The tree is expected to be clean here
# at all times -- run it BEFORE proposing a change, not after.
# ---------------------------------------------------------------------------
strict: $(SRC) $(HDR)
	$(CC) $(CFLAGS) $(NAMEDEF) -Werror -Wshadow -o $(BIN) $(SRC) $(LDLIBS)

# ---------------------------------------------------------------------------
# asan -- AddressSanitizer + UndefinedBehaviorSanitizer, into $(BIN)_asan so it
# sits alongside the normal binary rather than replacing it.
#
# Instruments every memory access and arithmetic operation, so an out-of-bounds
# read, a use-after-free or a signed overflow is reported AT THE MOMENT IT
# HAPPENS, with a stack trace -- instead of surfacing later as a wrong number
# whose cause is untraceable.  Larger and slower than the normal build, but
# only by a small factor -- fast enough to run whole recordings, so there is no
# reason to avoid it on performance grounds.
#
# -O1 rather than -O0 keeps the reports readable while staying fast enough to
# run whole recordings.  Run with ASAN_OPTIONS=detect_leaks=0 -- the program
# holds its buffers until exit by design, so leak checking reports them at
# teardown and says nothing useful.
# ---------------------------------------------------------------------------
asan: $(SRC) $(HDR)
	$(CC) $(BASEFLAGS) -O1 -g -fsanitize=address,undefined \
	      -DPPG_PROG_NAME='"$(BIN)_asan"' -o $(BIN)_asan $(SRC) $(LDLIBS)

# ---------------------------------------------------------------------------
# debug -- -O0 -g, into $(BIN)_debug so it too coexists with the normal binary.
#
# -g adds the symbols a debugger needs to name things.  -O0 is the part that
# matters more: at -O2 the optimiser folds constants, inlines functions and
# DISCARDS variables whose values it can prove, so stepping jumps around and a
# variable you want to inspect may not exist in the binary at all.  At -O0 what
# gdb shows matches the source line by line.  Same program, compiled so it can
# be watched.
# ---------------------------------------------------------------------------
debug: $(SRC) $(HDR)
	$(CC) $(BASEFLAGS) -O0 -g -DPPG_PROG_NAME='"$(BIN)_debug"' \
	      -o $(BIN)_debug $(SRC) $(LDLIBS)

# ---------------------------------------------------------------------------
# clean -- removes build artefacts AND any CSVs a run has dropped here.
#
# Run outputs do not belong in the source tree: they accumulate, they are stale
# the moment the code changes, and an old one can misrepresent what the current
# build produces.  The program writes its CSVs to the CURRENT WORKING DIRECTORY,
# so the remedy is to run it from somewhere else:
#
#     mkdir -p run && cd run && ../ppg_analysis -i <recording> ...
#
# This target is the safety net for when that is forgotten.
# ---------------------------------------------------------------------------
clean:
	rm -f $(BIN) $(BIN)_asan $(BIN)_debug
	rm -f ppg_analysis.csv RR_Data.csv INTP_RR_peak.csv INTP_RR_foot.csv INTP_RR_fm.csv
	rm -f *_ppg_analysis.csv *_RR_Data.csv

# Prints the banner above.  Delimited by the ==== rules rather than by line
# numbers, so editing the banner cannot leave the help printing the wrong lines.
help:
	@awk '/^# ={10,}/ { n++; next } n == 1' Makefile | sed 's/^# \{0,1\}//'
