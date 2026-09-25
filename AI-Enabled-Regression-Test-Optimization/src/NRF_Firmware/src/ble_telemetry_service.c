/*
 * Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
 * Original author: Dhanya Shree S
 * SPDX-License-Identifier: MIT
 *
 * Licensed under the MIT License. See LICENSE in the project root.
 */

/*
 * Device telemetry over BLE. See ble_telemetry_service.h for the wire format.
 *
 * Requires in prj.conf:
 *
 *     CONFIG_INIT_STACKS=y                # mem_used / mem_free
 *     CONFIG_THREAD_STACK_INFO=y          # mem_used / mem_free
 *
 * mem_used and mem_free are work-queue stack, not heap, so those two are what
 * they depend on: without them k_thread_stack_space_get cannot report, the
 * fields read 0 and the host treats memory as unavailable rather than as a
 * device with no memory in use. CONFIG_SYS_HEAP_RUNTIME_STATS only arms the
 * heap fallback in sample_memory(), which is compiled out here because
 * CONFIG_HEAP_MEM_POOL_SIZE is 0.
 *
 * BT_GATT_SERVICE_DEFINE below registers statically at boot, and so does the
 * audio service in main.c, so neither needs CONFIG_BT_GATT_DYNAMIC_DB -- and
 * prj.conf does not set it. Nothing else in the build depends on it either:
 * the options that do (the audio profiles, mesh, OTS, the mcumgr Bluetooth
 * transport) are all off here, and the Battery Service registers statically
 * as well.
 *
 * Built into the application via NRF_Firmware/CMakeLists.txt. Adding or
 * removing it changes the GATT attribute table, so bump HA_STATIC_ADDR in
 * main.c when you do -- a host with a cached database will not otherwise
 * re-discover the service.
 */

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/services/bas.h>
#include <zephyr/drivers/adc.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>

#include <string.h>

#include "ble_telemetry_service.h"

LOG_MODULE_REGISTER(ha_telemetry, LOG_LEVEL_INF);

/* ---------------------------------------------------------------- UUIDs */

#define TELEMETRY_SERVICE_UUID_VAL \
    BT_UUID_128_ENCODE(0x12345678, 0x1234, 0x5678, 0x1234, 0x56789abcdee0)

#define TELEMETRY_SNAPSHOT_UUID_VAL \
    BT_UUID_128_ENCODE(0x12345678, 0x1234, 0x5678, 0x1234, 0x56789abcdee1)

/* Read requires an encrypted link. This is what gives a pairing test
 * something to prove: an unpaired read must be refused and a paired read
 * must succeed. */
#define TELEMETRY_SECURE_UUID_VAL \
    BT_UUID_128_ENCODE(0x12345678, 0x1234, 0x5678, 0x1234, 0x56789abcdee2)

static struct bt_uuid_128 telemetry_service_uuid =
    BT_UUID_INIT_128(TELEMETRY_SERVICE_UUID_VAL);
static struct bt_uuid_128 telemetry_snapshot_uuid =
    BT_UUID_INIT_128(TELEMETRY_SNAPSHOT_UUID_VAL);
static struct bt_uuid_128 telemetry_secure_uuid =
    BT_UUID_INIT_128(TELEMETRY_SECURE_UUID_VAL);

/* Build id, readable without pairing: the host needs it to label a run
 * before it has done anything else. */
#define TELEMETRY_BUILD_UUID_VAL \
    BT_UUID_128_ENCODE(0x12345678, 0x1234, 0x5678, 0x1234, 0x56789abcdee3)

static struct bt_uuid_128 telemetry_build_uuid =
    BT_UUID_INIT_128(TELEMETRY_BUILD_UUID_VAL);

/* Set by CMake from git describe. The fallback matters: a tree with no
 * git history still builds, and the host must be able to tell an unknown
 * build from a known one rather than reading an empty string. */
#ifndef HA_FW_REVISION
#define HA_FW_REVISION "unversioned"
#endif

static const char build_id[] = HA_FW_REVISION;

/* ----------------------------------------------------------------- state */

/* How often to refresh and (if subscribed) notify. */
#define TELEMETRY_PERIOD_MS 1000

static struct ha_telemetry snapshot = {
    .version = HA_TELEMETRY_VERSION,
    .battery_pct = 0xFF,                     /* unknown until something sets it */
    .sync_latency_us = HA_SYNC_UNKNOWN,      /* no second device on this bench  */
    .current_ma = 0,                         /* no current sensor on the DK     */
};

/* Counters are touched from BLE callbacks and read from a timer, so keep the
 * updates atomic rather than read-modify-write on plain uint16_t. */
static atomic_t reconnects;
static atomic_t stream_errors;

static bool notify_enabled;

/* ---------------------------------------------------------- memory stats */

static void sample_memory(struct ha_telemetry *out)
{
    /*
     * What "memory in use" means on this application.
     *
     * The obvious source is the system heap, but CONFIG_HEAP_MEM_POOL_SIZE is
     * 0 here and nothing in this firmware calls k_malloc, so that number would
     * be a constant zero -- a real measurement of nothing, which reads as a
     * healthy device and is exactly the failure the telemetry service exists
     * to remove.
     *
     * The memory that actually moves is stack. The system work queue runs the
     * audio work handler, the DSP and the BLE notify path, so its high-water
     * mark is the figure worth watching: it climbs when processing deepens and
     * it is what overflows first.
     *
     * CONFIG_INIT_STACKS fills stacks with 0xAA so unused space is countable.
     * Without it k_thread_stack_space_get cannot tell used from untouched, so
     * the fields report 0 and the host treats memory as unmeasured.
     */
#if defined(CONFIG_INIT_STACKS) && defined(CONFIG_THREAD_STACK_INFO)
    size_t unused = 0;

    /*
     * Name the system work queue's thread rather than asking for the current
     * one. refresh() is called from the work queue and, once, from
     * ha_telemetry_init() on the main thread, so k_current_get() would sample
     * whichever thread happened to call and subtract its unused space from
     * CONFIG_SYSTEM_WORKQUEUE_STACK_SIZE -- reporting the work-queue stack
     * size minus another thread's headroom.
     */
    if (k_thread_stack_space_get(k_work_queue_thread_get(&k_sys_work_q),
                                 &unused) == 0) {
        size_t total = CONFIG_SYSTEM_WORKQUEUE_STACK_SIZE;

        out->mem_free = (uint32_t)unused;
        out->mem_used = (uint32_t)(total > unused ? total - unused : 0);

        return;
    }
#endif

#if (CONFIG_HEAP_MEM_POOL_SIZE > 0) && defined(CONFIG_SYS_HEAP_RUNTIME_STATS)
    /*
     * Fall back to the system heap where one exists. _system_heap is a
     * struct k_heap, not a struct sys_heap -- the sys_heap is its first
     * member. Declaring it as the wrong type happens to work by layout and
     * breaks silently if Zephyr ever reorders that struct, so take the member
     * explicitly.
     */
    extern struct k_heap _system_heap;

    struct sys_memory_stats stats;

    if (sys_heap_runtime_stats_get(&_system_heap.heap, &stats) == 0) {
        out->mem_used = (uint32_t)stats.allocated_bytes;
        out->mem_free = (uint32_t)stats.free_bytes;

        return;
    }
#endif

    /* Nothing measurable: report zero so the host reads "not available"
     * rather than "nothing in use". */
    out->mem_used = 0;
    out->mem_free = 0;
}

/* ----------------------------------------------------------------- bonds */

#if defined(CONFIG_BT_SMP)
static void count_one_bond(const struct bt_bond_info *info, void *user_data)
{
    ARG_UNUSED(info);

    uint8_t *total = user_data;

    (*total)++;
}
#endif

void ha_telemetry_refresh_bonds(void)
{
#if defined(CONFIG_BT_SMP)
    uint8_t total = 0;

    bt_foreach_bond(BT_ID_DEFAULT, count_one_bond, &total);

    snapshot.bond_count = total;
#else
    snapshot.bond_count = 0;
#endif
}

void ha_telemetry_set_security(uint8_t level)
{
    snapshot.sec_level = level;
}

/* --------------------------------------------------------------- battery */

/*
 * The supply rail, in millivolts, mapped onto a percentage.
 *
 * These are the limits of a rail, not of a cell. A product with a battery
 * would read a fuel gauge and get a state of charge; this reports where the
 * supply sits between a brown-out floor and a charged-cell ceiling, which
 * is a real measurement of a real thing and moves when the supply moves.
 *
 * The distinction matters enough to keep in the name: BATTERY_* would claim
 * more than the DK can know.
 */
#define HA_RAIL_EMPTY_MV   1800
#define HA_RAIL_FULL_MV    3600

static const struct adc_dt_spec rail_channel =
    ADC_DT_SPEC_GET(DT_PATH(zephyr_user));

static bool rail_ready;

static void battery_init(void)
{
    if (!adc_is_ready_dt(&rail_channel)) {
        printk("SAADC not ready; battery level stays unmeasured\n");

        return;
    }

    if (adc_channel_setup_dt(&rail_channel) < 0) {
        printk("SAADC channel setup failed; battery level stays unmeasured\n");

        return;
    }

    rail_ready = true;
}

static int read_rail_mv(void)
{
    int16_t raw = 0;
    struct adc_sequence sequence = {
        .buffer = &raw,
        .buffer_size = sizeof(raw),
    };

    if (!rail_ready) {
        return -ENODEV;
    }

    if (adc_sequence_init_dt(&rail_channel, &sequence) < 0) {
        return -EIO;
    }

    if (adc_read_dt(&rail_channel, &sequence) < 0) {
        return -EIO;
    }

    int32_t millivolts = raw;

    if (adc_raw_to_millivolts_dt(&rail_channel, &millivolts) < 0) {
        return -EIO;
    }

    return (int)millivolts;
}

static void sample_battery(struct ha_telemetry *out)
{
    int millivolts = read_rail_mv();

    if (millivolts < 0) {
        /* Unmeasured, and the host is told so rather than shown a default. */
        out->battery_pct = 0xFF;

        return;
    }

    if (millivolts <= HA_RAIL_EMPTY_MV) {
        out->battery_pct = 0;
    } else if (millivolts >= HA_RAIL_FULL_MV) {
        out->battery_pct = 100;
    } else {
        out->battery_pct = (uint8_t)(((millivolts - HA_RAIL_EMPTY_MV) * 100) /
                                     (HA_RAIL_FULL_MV - HA_RAIL_EMPTY_MV));
    }

    /*
     * Publish on the standard Battery Service too. A phone, a tracker or
     * any generic central reads 0x2A19 and knows nothing about this
     * project's telemetry characteristic.
     */
    bt_bas_set_battery_level(out->battery_pct);
}

/* -------------------------------------------------------------- sampling */

static void refresh(void)
{
    snapshot.version = HA_TELEMETRY_VERSION;
    snapshot.reconnects = (uint16_t)atomic_get(&reconnects);
    snapshot.stream_errors = (uint16_t)atomic_get(&stream_errors);

    sample_memory(&snapshot);
    sample_battery(&snapshot);

    ha_telemetry_refresh_bonds();
}

/* ----------------------------------------------------------------- GATT */

static ssize_t read_snapshot(struct bt_conn *conn,
                             const struct bt_gatt_attr *attr,
                             void *buf, uint16_t len, uint16_t offset)
{
    ARG_UNUSED(conn);

    /* Deliberately no refresh() here. Sampling from this thread would make
     * the memory figure depend on who is reading it. The work queue
     * refreshes the snapshot once a second, so a read returns whatever that
     * last sample recorded. */

    return bt_gatt_attr_read(conn, attr, buf, len, offset,
                             &snapshot, sizeof(snapshot));
}

static ssize_t read_bond_count(struct bt_conn *conn,
                               const struct bt_gatt_attr *attr,
                               void *buf, uint16_t len, uint16_t offset)
{
    ha_telemetry_refresh_bonds();

    return bt_gatt_attr_read(conn, attr, buf, len, offset,
                             &snapshot.bond_count,
                             sizeof(snapshot.bond_count));
}

static ssize_t read_build_id(struct bt_conn *conn,
                             const struct bt_gatt_attr *attr,
                             void *buf, uint16_t len, uint16_t offset)
{
    size_t length = strlen(build_id);

    if (length > HA_BUILD_ID_MAX) {
        length = HA_BUILD_ID_MAX;
    }

    return bt_gatt_attr_read(conn, attr, buf, len, offset,
                             build_id, length);
}

static void telemetry_ccc_changed(const struct bt_gatt_attr *attr,
                                  uint16_t value)
{
    ARG_UNUSED(attr);

    notify_enabled = (value == BT_GATT_CCC_NOTIFY);

    LOG_INF("telemetry notifications %s",
            notify_enabled ? "enabled" : "disabled");
}

BT_GATT_SERVICE_DEFINE(telemetry_svc,
    BT_GATT_PRIMARY_SERVICE(&telemetry_service_uuid),

    BT_GATT_CHARACTERISTIC(&telemetry_snapshot_uuid.uuid,
                           BT_GATT_CHRC_READ | BT_GATT_CHRC_NOTIFY,
                           BT_GATT_PERM_READ,
                           read_snapshot, NULL, &snapshot),

    BT_GATT_CCC(telemetry_ccc_changed,
                BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),

    /* Encrypted read. Built only when SMP is present -- without it the
     * permission could never be satisfied and every read would fail with
     * no way to pair, which is a worse failure than an absent
     * characteristic. */
#if defined(CONFIG_BT_SMP)
    BT_GATT_CHARACTERISTIC(&telemetry_secure_uuid.uuid,
                           BT_GATT_CHRC_READ,
                           BT_GATT_PERM_READ_ENCRYPT,
                           read_bond_count, NULL, NULL),
#endif

    BT_GATT_CHARACTERISTIC(&telemetry_build_uuid.uuid,
                           BT_GATT_CHRC_READ,
                           BT_GATT_PERM_READ,
                           read_build_id, NULL, NULL),
);

#define TELEMETRY_ATTR (&telemetry_svc.attrs[1])

/* ---------------------------------------------------------------- timer */

static void telemetry_work_handler(struct k_work *work);
static K_WORK_DEFINE(telemetry_work, telemetry_work_handler);

static void telemetry_timer_handler(struct k_timer *timer)
{
    ARG_UNUSED(timer);

    /* Notifying from a timer ISR is not allowed; hand off to the work queue. */
    k_work_submit(&telemetry_work);
}

static K_TIMER_DEFINE(telemetry_timer, telemetry_timer_handler, NULL);

static void telemetry_work_handler(struct k_work *work)
{
    ARG_UNUSED(work);

    refresh();

    if (!notify_enabled) {
        return;
    }

    int err = bt_gatt_notify(NULL, TELEMETRY_ATTR, &snapshot, sizeof(snapshot));

    if (err && err != -ENOTCONN) {
        LOG_WRN("telemetry notify failed (%d)", err);
    }
}

/* ------------------------------------------------------------ public API */

void ha_telemetry_note_reconnect(void)
{
    atomic_inc(&reconnects);
}

void ha_telemetry_note_stream_error(void)
{
    atomic_inc(&stream_errors);
}

void ha_telemetry_set_sync_latency(uint16_t microseconds)
{
    snapshot.sync_latency_us = microseconds;
}

void ha_telemetry_set_current(uint16_t milliamps)
{
    snapshot.current_ma = milliamps;
}

int ha_telemetry_init(void)
{
    BUILD_ASSERT(sizeof(struct ha_telemetry) <= 20,
                 "snapshot must fit one notification at the default MTU");

    atomic_set(&reconnects, 0);
    atomic_set(&stream_errors, 0);

    snapshot.sec_level = 1;   /* BT_SECURITY_L1 until something raises it */

    /* Before the first refresh(), which samples the battery. Without this
     * the first snapshot reports unmeasured and the Battery Service sits at
     * its default until the timer comes round. */
    battery_init();

    ha_telemetry_refresh_bonds();

    refresh();

    k_timer_start(&telemetry_timer,
                  K_MSEC(TELEMETRY_PERIOD_MS),
                  K_MSEC(TELEMETRY_PERIOD_MS));

    LOG_INF("telemetry service ready (%u byte snapshot), build %s",
            (unsigned)sizeof(struct ha_telemetry), build_id);

    printk("Firmware build id: %s\n", build_id);

    return 0;
}
