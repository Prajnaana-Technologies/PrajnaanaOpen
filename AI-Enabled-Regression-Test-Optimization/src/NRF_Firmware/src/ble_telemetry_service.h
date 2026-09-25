/*
 * Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
 * Original author: Dhanya Shree S
 * SPDX-License-Identifier: MIT
 *
 * Licensed under the MIT License. See LICENSE in the project root.
 */

/*
 * Device telemetry over BLE -- the metrics the risk engine scores.
 *
 * This service is what makes the risk engine's inputs real. Without it the
 * host reports every metric as unmeasured, and the risk engine scores an
 * unmeasured metric as risk rather than as health -- see
 * regression/change_detection/risk_engine.py.
 *
 * Service    12345678-1234-5678-1234-56789abcdee0
 *   dee1     snapshot, read + notify, one packed struct (20 bytes)
 *   dee2     bond count, read, ENCRYPTION REQUIRED
 *   dee3     build id string, read
 *
 * dee3 is what lets the host label a run without anyone typing the
 * firmware version. The standard Device Information Service (0x180A)
 * covers the same ground and is the interoperable choice for a product;
 * this lives here because the service is already present and the string
 * comes straight from the build.
 *
 * dee2 exists so pairing can be tested for its effect rather than for its
 * return code: an unpaired read of it must fail, and the same read after
 * pairing must succeed. Without a characteristic that actually demands
 * encryption, a "pairing works" test proves only that the call returned.
 *
 * One characteristic rather than one per metric, deliberately:
 *   - a single read returns a consistent set, with no tearing between values
 *   - 20 bytes fits in one notification at the default MTU of 23 (20 usable),
 *     so no chunking and no reassembly on the host
 */

#ifndef BLE_TELEMETRY_SERVICE_H
#define BLE_TELEMETRY_SERVICE_H

#include <stdint.h>

/* Bump when the struct layout changes. The host rejects versions it does not
 * know rather than silently misreading fields. */
#define HA_TELEMETRY_VERSION 3

/* Reported when there is no binaural sync measurement to publish. A second
 * device is needed for a real one, so on a single-board bench this is what
 * the field carries. Host-side counterpart: telemetry.SYNC_UNKNOWN. */
#define HA_SYNC_UNKNOWN 0xFFFF

/*
 * Little-endian, packed. Keep at or under 20 bytes or it stops fitting in a
 * single default-MTU notification.
 *
 * mem_used / mem_free are system work queue stack, not heap. The application
 * never calls k_malloc, so heap use would be a constant zero -- a real
 * measurement of nothing. Stack is what moves under load and overflows first.
 * See sample_memory() in the .c for the full reasoning.
 *
 * Version 2 renamed these from heap_used / heap_free. The layout was
 * unchanged; the bump exists so a v1 host refuses the reading rather than
 * quietly relabelling stack bytes as heap bytes.
 *
 * Version 3 -- what HA_TELEMETRY_VERSION is now -- appended bond_count and
 * sec_level, taking the struct from 18 bytes to 20.
 *
 * Host-side parser: regression/ble/telemetry.py -- keep the two in step.
 * tests/test_telemetry.py guards the layout; run it after any change here.
 */
struct ha_telemetry {
    uint8_t  version;          /* HA_TELEMETRY_VERSION                      */
    uint8_t  battery_pct;      /* 0-100, 0xFF if unknown                    */
    uint16_t current_ma;       /* supply current, 0 if not measured         */
    uint16_t sync_latency_us;  /* binaural sync error, HA_SYNC_UNKNOWN if
                                * not measured                              */
    uint16_t reconnects;       /* link drops since boot                     */
    uint16_t stream_errors;    /* audio writes dropped since boot           */
    uint32_t mem_used;         /* bytes in use, 0 if not measured           */
    uint32_t mem_free;         /* bytes still available                     */
    uint8_t  bond_count;       /* stored bonds, 0..CONFIG_BT_MAX_PAIRED     */
    uint8_t  sec_level;        /* current link security, 1..4 (BT_SECURITY) */
} __packed;

/* Longest build id the characteristic will return. git describe output is
 * comfortably shorter; anything longer is truncated rather than refused. */
#define HA_BUILD_ID_MAX 32

/* Register the service and start the sampling timer. Call from main(). */
int ha_telemetry_init(void);

/* Call from your bt disconnected callback. */
void ha_telemetry_note_reconnect(void);

/* Call when an audio write has to be dropped (e.g. ring buffer full). */
void ha_telemetry_note_stream_error(void);

/* Publish the most recent binaural sync measurement. */
void ha_telemetry_set_sync_latency(uint16_t microseconds);

/* Publish a measured supply current; leave unset if there is no sensor. */
void ha_telemetry_set_current(uint16_t milliamps);

/* Call from your bt security_changed callback. */
void ha_telemetry_set_security(uint8_t level);

/* Recount stored bonds. Call after pairing completes or a bond is deleted. */
void ha_telemetry_refresh_bonds(void);

#endif /* BLE_TELEMETRY_SERVICE_H */
