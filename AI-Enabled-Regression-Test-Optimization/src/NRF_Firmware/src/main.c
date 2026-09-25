/*
 * Copyright (c) 2026 Prajnaana Technologies Pvt. Ltd.
 * SPDX-License-Identifier: MIT
 *
 * Licensed under the MIT License. See LICENSE in the project root.
 */

/*
 * SmartEarbuds / Zephyr_Earbuds -- BLE audio service with DSP loopback.
 *
 * Host streams raw PCM16 to the write characteristic; this firmware runs wide
 * dynamic range compression over it and notifies the processed audio back.
 * That loopback is what makes the host's test_audio_dsp meaningful: without
 * it the test can only ever skip.
 *
 * Service   12345678-1234-5678-1234-56789abcdef0
 *   def1    write, write-without-response   host -> device, raw PCM16
 *   def2    notify                          device -> host, processed PCM16
 *
 * Notifying happens on the system work queue, never inside the ATT write
 * callback -- notifying from there can block the BLE stack and drop audio.
 */

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>
#include <zephyr/sys/ring_buffer.h>

#include <zephyr/settings/settings.h>

#include "ble_telemetry_service.h"
#include "dsp_wdrc.h"

/* BT_LE_ADV_CONN_NAME was removed in Zephyr 4.x. The name now has to go
 * into the advertising payload explicitly, and the host scans for it
 * (DEVICE_NAME_MATCH in regression/ble/ble_audio.py). */
#define DEVICE_NAME     CONFIG_BT_DEVICE_NAME
#define DEVICE_NAME_LEN (sizeof(DEVICE_NAME) - 1)

static const struct bt_data ad[] = {
    BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)),
    BT_DATA(BT_DATA_NAME_COMPLETE, DEVICE_NAME, DEVICE_NAME_LEN),
};

/*
 * The original firmware dropped TX power and force-disconnected 10 seconds
 * after every connection, to exercise reconnect handling.
 *
 * That must not be on by default. One audio stream is 4000 bytes sent as
 * about 32 writes of up to 128 bytes, 5 ms apart -- roughly 0.2 s, plus a
 * 0.25 s quiet period before the host stops listening. A single stream now
 * finishes well inside 10 s, but the host holds one connection for the whole
 * test session, so a drop at 10 s lands in the middle of some later test.
 *
 * Set to 1 only when you specifically want to test disconnect handling.
 */
#define HA_DISCONNECT_TEST 0
#define HA_DISCONNECT_TEST_SECONDS 10

/*
 * Windows caches a BLE device's GATT database by address and does not
 * reliably notice when the layout changes. After adding the notify
 * characteristic, Windows kept serving handles from the previous build --
 * subscribe failed with "Invalid Handle", and forcing an uncached read
 * failed with "Catastrophic failure". Clearing it needs an elevated
 * pnputil call on the host.
 *
 * Advertising under a fresh address sidesteps that entirely: Windows sees
 * a device it has never met and discovers the database properly. The host
 * scans by name (DEVICE_NAME_MATCH), not by address, so nothing on that
 * side changes.
 *
 * Must be a static random address: the two most significant bits of the
 * first octet have to be 1, so the first octet must be >= 0xC0.
 * Bump this whenever you change the GATT layout.
 */
#define HA_SET_STATIC_ADDR 1
/* Bump whenever the GATT attribute table changes, so a host holding a
 * cached database against the previous address rediscovers the new layout.
 * RELEASE_NOTES.md records which build each bump belongs to. */
#define HA_STATIC_ADDR     "C1:F5:B1:81:20:F2"

/* ================= UUIDs ================= */

/* BT_UUID_INIT_128 takes bytes little-endian: this reads as
 * 12345678-1234-5678-1234-56789abcdef0 and matches the host constants in
 * regression/ble/ble_audio.py. */
static struct bt_uuid_128 service_uuid = BT_UUID_INIT_128(
    0xf0, 0xde, 0xbc, 0x9a,
    0x78, 0x56,
    0x34, 0x12,
    0x78, 0x56,
    0x34, 0x12, 0x78, 0x56, 0x34, 0x12);

static struct bt_uuid_128 write_uuid = BT_UUID_INIT_128(
    0xf1, 0xde, 0xbc, 0x9a,
    0x78, 0x56,
    0x34, 0x12,
    0x78, 0x56,
    0x34, 0x12, 0x78, 0x56, 0x34, 0x12);

/* ...def2 -- the notify characteristic the previous build was missing. */
static struct bt_uuid_128 notify_uuid = BT_UUID_INIT_128(
    0xf2, 0xde, 0xbc, 0x9a,
    0x78, 0x56,
    0x34, 0x12,
    0x78, 0x56,
    0x34, 0x12, 0x78, 0x56, 0x34, 0x12);

/* ================= STATE ================= */

/* Host bursts writes of up to 128 bytes; buffer generously so nothing is
 * dropped while the work queue catches up. */
#define AUDIO_RING_BYTES    4096
#define AUDIO_BLOCK_SAMPLES 64

RING_BUF_DECLARE(audio_ring, AUDIO_RING_BYTES);

/*
 * An odd trailing byte is half a PCM16 sample and has to wait for the next
 * write. It is held here rather than written back into the ring: the ring has
 * a single producer (audio_write), and a put from the work handler would
 * append the byte AFTER data that arrived later, reordering the stream.
 * Today's writes are all even-sized, so this is a guard rather than a path
 * that runs.
 *
 * It lives beside the ring because it has to be discarded with it. As a
 * static inside the work handler it survived a reset, so a leftover byte from
 * one connection was put in front of the next stream and shifted every sample
 * by one byte.
 */
static uint8_t audio_carry;
static bool audio_have_carry;

static void audio_stream_reset(void)
{
    ring_buf_reset(&audio_ring);

    audio_carry = 0;
    audio_have_carry = false;
}

static struct bt_conn *current_conn;
static bool notify_enabled;

static uint32_t total_in;
static uint32_t total_out;
static uint32_t dropped;
static uint32_t seconds_counter;

/* ================= GATT ================= */

static void audio_ccc_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
    ARG_UNUSED(attr);

    notify_enabled = (value == BT_GATT_CCC_NOTIFY);

    printk("Notifications %s\n", notify_enabled ? "enabled" : "disabled");

    if (notify_enabled) {
        /* Fresh subscriber: start the compressor from a known state so the
         * first samples are not shaped by the previous stream. */
        wdrc_reset();
        audio_stream_reset();
    }
}

static void audio_work_handler(struct k_work *work);
static K_WORK_DEFINE(audio_work, audio_work_handler);

static ssize_t audio_write(struct bt_conn *conn,
                           const struct bt_gatt_attr *attr,
                           const void *buf, uint16_t len,
                           uint16_t offset, uint8_t flags)
{
    ARG_UNUSED(attr);
    ARG_UNUSED(offset);
    ARG_UNUSED(flags);

    current_conn = conn;
    total_in += len;

    uint32_t written = ring_buf_put(&audio_ring, buf, len);

    if (written < len) {
        /* Host is streaming faster than the DSP drains it. Count it rather
         * than truncating silently -- a full ring is a real finding. */
        dropped += (len - written);
        ha_telemetry_note_stream_error();
        printk("Ring full, dropped %u bytes (total %u)\n",
               (unsigned)(len - written), (unsigned)dropped);
    }

    k_work_submit(&audio_work);

    return len;
}

/*
 * Write permission on the audio characteristic.
 *
 * HA_OPEN_WRITE=1 (the default) accepts audio from anything in radio range.
 * That is what lets an unattended bench run work with no pairing step, and it
 * is not acceptable in a shipped product: any nearby radio can push samples
 * into the DSP.
 *
 * Build with -DHA_OPEN_WRITE=0 to require an encrypted link instead. The host
 * must then bond before streaming, so expect the first connection after a
 * flash to fail until it does.
 */
#ifndef HA_OPEN_WRITE
#define HA_OPEN_WRITE 1
#endif

#if HA_OPEN_WRITE
#define HA_AUDIO_WRITE_PERM BT_GATT_PERM_WRITE
#else
#define HA_AUDIO_WRITE_PERM BT_GATT_PERM_WRITE_ENCRYPT
#endif

BT_GATT_SERVICE_DEFINE(audio_svc,
    BT_GATT_PRIMARY_SERVICE(&service_uuid),

    BT_GATT_CHARACTERISTIC(&write_uuid.uuid,
                           BT_GATT_CHRC_WRITE | BT_GATT_CHRC_WRITE_WITHOUT_RESP,
                           HA_AUDIO_WRITE_PERM,
                           NULL, audio_write, NULL),

    BT_GATT_CHARACTERISTIC(&notify_uuid.uuid,
                           BT_GATT_CHRC_NOTIFY,
                           BT_GATT_PERM_NONE,
                           NULL, NULL, NULL),

    BT_GATT_CCC(audio_ccc_changed,
                BT_GATT_PERM_READ | BT_GATT_PERM_WRITE),
);

/* attrs: 0 service, 1/2 write chrc+value, 3/4 notify chrc+value, 5 CCC */
#define AUDIO_NOTIFY_ATTR (&audio_svc.attrs[4])

/* ================= PROCESSING ================= */

static uint16_t notify_chunk_size(void)
{
    /* 3 bytes of ATT header. Default MTU 23 leaves 20; this device negotiates
     * 247, which leaves 244 -- but nothing ever sends that much: the work
     * handler holds one 128-byte block, so notifications are at most one
     * 128-byte block. The host sizes its writes separately -- see MAX_CHUNK
     * in ble_audio.py. */
    uint16_t mtu = current_conn ? bt_gatt_get_mtu(current_conn) : 23;
    uint16_t payload = (mtu > 3) ? (mtu - 3) : 20;

    /* Keep it even so a PCM16 sample is never split across notifications. */
    return payload & ~(uint16_t)1;
}

static void audio_work_handler(struct k_work *work)
{
    ARG_UNUSED(work);

    int16_t block[AUDIO_BLOCK_SAMPLES];
    uint8_t *bytes = (uint8_t *)block;

    /* audio_carry holds the odd trailing byte left by the previous pass; it
     * sits beside the ring so a reset discards both. */

    while (!ring_buf_is_empty(&audio_ring)) {

        uint32_t offset = 0;

        if (audio_have_carry) {
            bytes[0] = audio_carry;
            audio_have_carry = false;
            offset = 1;
        }

        uint32_t got = offset + ring_buf_get(&audio_ring, bytes + offset,
                                             sizeof(block) - offset);

        if (got < sizeof(int16_t)) {
            if (got == 1u) {
                audio_carry = bytes[0];
                audio_have_carry = true;
            }

            break;
        }

        if (got & 1u) {
            audio_carry = bytes[got - 1];
            audio_have_carry = true;
            got -= 1;
        }

        wdrc_process(block, got / sizeof(int16_t));

        if (!notify_enabled || !current_conn) {
            continue;   /* nobody subscribed; discard the processed audio */
        }

        uint16_t chunk = notify_chunk_size();
        uint8_t *out = (uint8_t *)block;

        for (uint32_t sent = 0; sent < got; sent += chunk) {

            uint16_t n = (got - sent < chunk) ? (uint16_t)(got - sent) : chunk;

            int err = bt_gatt_notify(current_conn, AUDIO_NOTIFY_ATTR,
                                     out + sent, n);

            if (err == -ENOMEM || err == -EAGAIN) {
                /* Stack buffers are full. Yield and retry this chunk rather
                 * than spinning or dropping it. */
                k_sleep(K_MSEC(2));
                sent -= chunk;
                continue;
            }

            if (err) {
                printk("Notify failed (%d)\n", err);
                break;
            }

            total_out += n;
        }
    }
}

/* ================= ADVERTISING ================= */

static void adv_work_handler(struct k_work *work);

/* Declared before the handler body so the retry path can reschedule it. */
static K_WORK_DELAYABLE_DEFINE(adv_work, adv_work_handler);

static void adv_work_handler(struct k_work *work)
{
    ARG_UNUSED(work);

    int err = bt_le_adv_start(BT_LE_ADV_CONN_FAST_1, ad, ARRAY_SIZE(ad),
                              NULL, 0);

    if (err == -EALREADY) {
        return;                 /* already advertising, nothing to do */
    }

    if (err) {
        printk("Advertising restart failed (%d), retrying\n", err);
        k_work_schedule(&adv_work, K_SECONDS(1));
        return;
    }

    printk("Advertising restarted\n");
}

/* ================= CONNECTION ================= */

static void connected(struct bt_conn *conn, uint8_t err)
{
    if (err) {
        printk("Connection failed (%u)\n", err);
        return;
    }

    current_conn = bt_conn_ref(conn);

    seconds_counter = 0;
    total_in = 0;
    total_out = 0;
    dropped = 0;

    wdrc_reset();
    audio_stream_reset();

    printk("Connected\n");
}

static void security_changed(struct bt_conn *conn, bt_security_t level,
                             enum bt_security_err err)
{
    ARG_UNUSED(conn);

    if (err) {
        printk("Security change failed (level %u, err %u)\n", level, err);
        return;
    }

    printk("Security level %u\n", level);

    /* Surfaces on the telemetry characteristic so a host test can assert
     * the link is actually encrypted, not just that pairing returned. */
    ha_telemetry_set_security((uint8_t)level);
}

static void pairing_complete(struct bt_conn *conn, bool bonded)
{
    ARG_UNUSED(conn);

    printk("Paired (bonded=%d)\n", (int)bonded);

    ha_telemetry_refresh_bonds();
}

static void pairing_failed(struct bt_conn *conn, enum bt_security_err reason)
{
    ARG_UNUSED(conn);

    printk("Pairing failed (reason %u)\n", reason);
}

static struct bt_conn_auth_info_cb auth_info_cb = {
    .pairing_complete = pairing_complete,
    .pairing_failed = pairing_failed,
};

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
    ARG_UNUSED(conn);

    printk("Disconnected (reason %u) | in %u out %u dropped %u\n",
           reason, (unsigned)total_in, (unsigned)total_out, (unsigned)dropped);

    /* Feeds the risk engine "retry" metric. */
    ha_telemetry_note_reconnect();

    /* The link is gone, so it is no longer encrypted. Leaving the old
     * level published would let a test read a stale 2 on an open link. */
    ha_telemetry_set_security(1);

    if (current_conn) {
        bt_conn_unref(current_conn);
        current_conn = NULL;
    }

    notify_enabled = false;
    audio_stream_reset();

    /* Resume advertising, or the device is invisible until reboot and every
     * reconnect fails.
     *
     * Deferred to a work item on purpose. Calling bt_le_adv_start() directly
     * from this callback does not reliably work -- the connection is not
     * fully torn down yet, the call fails, and the device goes silent until
     * reboot. That was the real cause of the board "randomly" disappearing
     * between test runs.
     */
    k_work_schedule(&adv_work, K_MSEC(500));
}

BT_CONN_CB_DEFINE(conn_callbacks) = {
    .connected = connected,
    .disconnected = disconnected,
    .security_changed = security_changed,
};

/* ================= MAIN ================= */

#if HA_SET_STATIC_ADDR
static void set_identity_address(void)
{
    bt_addr_le_t addr;

    int err = bt_addr_le_from_str(HA_STATIC_ADDR, "random", &addr);

    if (err) {
        printk("Bad static address %s (%d)\n", HA_STATIC_ADDR, err);
        return;
    }

    /* Must run before bt_enable() to become the default identity. */
    err = bt_id_create(&addr, NULL);

    if (err < 0) {
        printk("bt_id_create failed (%d); using the built-in address\n", err);
    } else {
        printk("Identity address set to %s\n", HA_STATIC_ADDR);
    }
}
#endif

int main(void)
{
    int err;

    printk("\n=== SmartEarbuds: BLE audio DSP loopback ===\n");

#if HA_SET_STATIC_ADDR
    set_identity_address();
#endif

    err = bt_enable(NULL);
    if (err) {
        printk("Bluetooth init failed (%d)\n", err);
        return 0;
    }

    printk("Bluetooth initialized\n");

    /* Loads stored bonds. Without this a bond survives in flash but the
     * stack does not know about it, so every reconnect re-pairs and the
     * bonding test cannot tell bonding from pairing. */
    if (IS_ENABLED(CONFIG_SETTINGS)) {
        settings_load();
    }

    bt_conn_auth_info_cb_register(&auth_info_cb);

    /* Must follow bt_enable(). Publishes work-queue stack use, link drops
     * and stream errors on the telemetry characteristic once a second;
     * without it the host reports every scored metric as unmeasured. */
    err = ha_telemetry_init();
    if (err) {
        printk("Telemetry init failed (%d); metrics read as unmeasured\n", err);
    }

    wdrc_reset();

    err = bt_le_adv_start(BT_LE_ADV_CONN_FAST_1, ad, ARRAY_SIZE(ad), NULL, 0);
    if (err) {
        printk("Advertising failed (%d)\n", err);
        return 0;
    }

    printk("Advertising started as '%s'\n", CONFIG_BT_DEVICE_NAME);

#if HA_OPEN_WRITE
    printk("WARNING: audio write is unauthenticated (HA_OPEN_WRITE=1).\n");
    printk("         Bench only. Build -DHA_OPEN_WRITE=0 to require encryption.\n");
#endif

#if HA_DISCONNECT_TEST
    printk("WARNING: disconnect test enabled, dropping link every %d s.\n",
           HA_DISCONNECT_TEST_SECONDS);
    printk("         The host holds one connection per session, so each\n");
    printk("         drop interrupts whichever test is running.\n");
#endif

    while (1) {

        k_sleep(K_SECONDS(1));
        seconds_counter++;

        if (current_conn && (total_in || total_out)) {
            printk("Audio: in %u  out %u  dropped %u\n",
                   (unsigned)total_in, (unsigned)total_out, (unsigned)dropped);
        }

#if HA_DISCONNECT_TEST
        if (current_conn && seconds_counter == HA_DISCONNECT_TEST_SECONDS) {
            printk("Disconnect test: forcing disconnect\n");
            bt_conn_disconnect(current_conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
        }
#endif
    }

    return 0;
}
