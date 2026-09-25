# Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
# SPDX-License-Identifier: MIT
#
# Licensed under the MIT License. See LICENSE in the project root.

cmake_minimum_required(VERSION 3.20.0)

# Devicetree additions, named explicitly rather than left to the
# boards/<board>.overlay convention.
#
# The convention finds this file on its own: for the hardware-model-v2 board
# target "nrf52840dk/nrf52840" Zephyr looks for
# boards/nrf52840dk_nrf52840.overlay, which is exactly this file
# (zephyr_build_string and zephyr_file(CONF_FILES ...) in NCS v3.4.0's
# cmake/modules; the same holds upstream).
#
# Naming it here is explicit and costs nothing: the same overlay is applied
# either way.
#
# Must be set before find_package(Zephyr).
set(EXTRA_DTC_OVERLAY_FILE
    "${CMAKE_CURRENT_SOURCE_DIR}/boards/nrf52840dk_nrf52840.overlay")

find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(AI_TEST_EARBUDS)

# ---------------------------------------------------------------------------
# Build options
#
# Bench builds accept unauthenticated audio writes, so an unattended run needs
# no pairing step; a product build must not. SECURITY.md documents
#
#     west build -b nrf52840dk/nrf52840 -- -DHA_OPEN_WRITE=0
#
# and everything after "--" is a CMake variable, so it has to be forwarded to
# the compiler here. Without that, main.c's "#ifndef HA_OPEN_WRITE" defaults
# it back to 1, the write characteristic keeps BT_GATT_PERM_WRITE, and the
# "secure" image is byte-identical to the bench one.
# ---------------------------------------------------------------------------
set(HA_OPEN_WRITE "1" CACHE STRING
    "1: audio writes need no encryption (bench). 0: require an encrypted link.")

if(NOT HA_OPEN_WRITE MATCHES "^[01]$")
    message(FATAL_ERROR
        "HA_OPEN_WRITE must be 0 or 1, not '${HA_OPEN_WRITE}'")
endif()

if(HA_OPEN_WRITE STREQUAL "0")
    message(STATUS "Audio write requires an encrypted link (HA_OPEN_WRITE=0)")
else()
    message(WARNING
        "Audio write is unauthenticated (HA_OPEN_WRITE=1). Bench only; "
        "build with -DHA_OPEN_WRITE=0 for anything that leaves the lab.")
endif()

# ---------------------------------------------------------------------------
# Build identity
#
# The board reports what it is running, so nobody has to remember or type it.
# The id is "<git describe>+<source hash>". Only the "+hash" half is a
# property of the source: it is the same for identical sources at any commit.
# The describe half moves with every commit, and firmware_diff.py compares
# whole ids, so identical sources rebuilt at a new commit read as an unknown
# change rather than as no change.
#
# describe is asked without --dirty. The "+hash" half already separates two
# images built from different sources, and it is computed from the files
# that go into the image and nothing else, so a "-dirty" marker says nothing
# about the image while firing on any edit anywhere in the tree: a README
# change would label the firmware modified. The determinism KPI compares
# runs sharing an id, and the hash is what makes that comparison sound.
# ---------------------------------------------------------------------------
find_package(Git QUIET)

if(GIT_FOUND)
    execute_process(
        COMMAND ${GIT_EXECUTABLE} describe --always --abbrev=8
        WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        OUTPUT_VARIABLE HA_FW_REVISION
        OUTPUT_STRIP_TRAILING_WHITESPACE
        ERROR_QUIET
    )
endif()

if(NOT HA_FW_REVISION)
    set(HA_FW_REVISION "unversioned")
endif()

# A content hash of everything that goes into the image.
#
# git describe alone is not enough to tell two builds apart: it returns the
# same "<commit>-dirty" for every uncommitted state, so a build before an edit
# and a build after it carry identical ids. The host keys its firmware
# fingerprints on this id, so identical ids mean no comparison is possible --
# which is exactly what happened on the bench.
#
# Hashing the sources fixes that. Same sources always hash the same, any edit
# changes it, and no commit is required.
file(GLOB HA_HASHED_SOURCES
    ${CMAKE_CURRENT_SOURCE_DIR}/src/*.c
    ${CMAKE_CURRENT_SOURCE_DIR}/src/*.h
    ${CMAKE_CURRENT_SOURCE_DIR}/boards/*.overlay
)

list(APPEND HA_HASHED_SOURCES
    ${CMAKE_CURRENT_SOURCE_DIR}/prj.conf
)

list(SORT HA_HASHED_SOURCES)

set(HA_CONTENT "")

# Hashed as text with CRLF normalised to LF, so the id is a property of the
# source and not of the checkout. file(SHA256) hashes raw bytes, which gives
# one id for a Windows checkout and another for the same commit on Linux or
# in CI: nobody rebuilding can reproduce the id a RELEASE_NOTES heading
# names, and section_for falls back to the whole note.
foreach(HA_FILE ${HA_HASHED_SOURCES})
    if(EXISTS ${HA_FILE})
        file(READ ${HA_FILE} HA_FILE_TEXT)
        string(REPLACE "\r\n" "\n" HA_FILE_TEXT "${HA_FILE_TEXT}")
        string(SHA256 HA_FILE_HASH "${HA_FILE_TEXT}")
        string(APPEND HA_CONTENT "${HA_FILE_HASH}")
    endif()
endforeach()

# Build options shape the image as much as the sources do: an open-write
# build and an encrypted-write build have identical sources, so without
# this they would share a build id -- and the host keys its fingerprints
# on that.
#
# Only the non-default value is folded in. Appending unconditionally moves
# the default build's id as well, so the delivered firmware.hex, the release
# note, the README and a test all name a build the source does not produce.
# The distinction the hash exists to make is between the two options, and a
# marker on one of them makes it just as well as a marker on both.
if(NOT HA_OPEN_WRITE STREQUAL "1")
    string(APPEND HA_CONTENT "HA_OPEN_WRITE=${HA_OPEN_WRITE}")
endif()

string(SHA256 HA_SOURCE_HASH "${HA_CONTENT}")
string(SUBSTRING ${HA_SOURCE_HASH} 0 8 HA_SOURCE_HASH)

# The hash above is computed when CMake CONFIGURES, not when it builds. An
# incremental build after an edit would otherwise recompile the new code and
# bake the stale id into it: different firmware, identical build id -- the
# same collision the content hash exists to prevent, one level further down.
#
# It matters because the host keys on this id. Two different images sharing
# one id means the determinism KPI compares builds that are not the same
# build, and firmware_diff overwrites the stored fingerprint of one with the
# other.
#
# Listing the hashed files as configure dependencies makes any edit to them
# re-run configure, so the id is recomputed before it is compiled in.
set_property(DIRECTORY APPEND PROPERTY
    CMAKE_CONFIGURE_DEPENDS ${HA_HASHED_SOURCES})

set(HA_FW_REVISION "${HA_FW_REVISION}+${HA_SOURCE_HASH}")

message(STATUS "Firmware build id: ${HA_FW_REVISION}")

# ble_telemetry_service.c publishes the metrics the host risk engine scores.
# Without it the device exposes no telemetry characteristic, the host reports
# power/memory/sync/retry as unmeasured, and test selection has nothing to
# select on.
target_sources(app PRIVATE
    src/main.c
    src/dsp_wdrc.c
    src/ble_telemetry_service.c
)

target_compile_definitions(app PRIVATE
    HA_FW_REVISION="${HA_FW_REVISION}"
    HA_OPEN_WRITE=${HA_OPEN_WRITE}
)
