#ifndef KISS_H__
#define KISS_H__

#include <stdint.h>
#include <stddef.h>

// Type byte: high nibble is the port, low nibble the command.
#define KISS_MASK_PORT 0xF0
#define KISS_MASK_CMD  0x0F

#define KISS_CMD_DATA        0x00
#define KISS_CMD_TXDELAY     0x01
#define KISS_CMD_PERSISTENCE 0x02
#define KISS_CMD_SLOTTIME    0x03
#define KISS_CMD_TXTAIL      0x04
#define KISS_CMD_FULLDUPLEX  0x05
#define KISS_CMD_SETHARDWARE 0x06
#define KISS_CMD_RETURN      0xFF

// SetHardware sub-commands (host to modem).
#define HW_CMD_GET_RANDOM        0x02
#define HW_CMD_SET_RADIO         0x09
#define HW_CMD_SET_TX_POWER      0x0A
#define HW_CMD_GET_RADIO         0x0B
#define HW_CMD_GET_TX_POWER      0x0C
#define HW_CMD_GET_CURRENT_RSSI  0x0D
#define HW_CMD_IS_CHANNEL_BUSY   0x0E
#define HW_CMD_GET_AIRTIME       0x0F
#define HW_CMD_GET_NOISE_FLOOR   0x10
#define HW_CMD_GET_VERSION       0x11
#define HW_CMD_GET_STATS         0x12
#define HW_CMD_GET_BATTERY       0x13
#define HW_CMD_GET_MCU_TEMP      0x14
#define HW_CMD_GET_SENSORS       0x15
#define HW_CMD_GET_DEVICE_NAME   0x16
#define HW_CMD_PING              0x17
#define HW_CMD_REBOOT            0x18
#define HW_CMD_SET_SIGNAL_REPORT 0x19
#define HW_CMD_GET_SIGNAL_REPORT 0x1A

// SetHardware responses (modem to host).
#define HW_RESP_RANDOM        0x82
#define HW_RESP_RADIO         0x8B
#define HW_RESP_TX_POWER      0x8C
#define HW_RESP_CURRENT_RSSI  0x8D
#define HW_RESP_CHANNEL_BUSY  0x8E
#define HW_RESP_AIRTIME       0x8F
#define HW_RESP_NOISE_FLOOR   0x90
#define HW_RESP_VERSION       0x91
#define HW_RESP_STATS         0x92
#define HW_RESP_DEVICE_NAME   0x96
#define HW_RESP_PONG          0x97
// MeshCore's modem answers SET_SIGNAL_REPORT with HW_RESP(HW_CMD_GET_SIGNAL_REPORT),
// that is 0x19 | 0x80 = 0x99. The protocol document lists this response as 0x9A,
// but both MeshCore's own firmware and the meshcore-go client use 0x99, and the
// client only enables its data/signal pairing on seeing it.
#define HW_RESP_SIGNAL_REPORT 0x99
#define HW_RESP_OK            0xF0
#define HW_RESP_ERROR         0xF1
#define HW_RESP_TX_DONE       0xF8
#define HW_RESP_RX_META       0xF9

// Error codes carried by HW_RESP_ERROR.
#define HW_ERR_INVALID_LENGTH 0x01
#define HW_ERR_INVALID_PARAM  0x02
#define HW_ERR_NO_CALLBACK    0x03
#define HW_ERR_UNKNOWN_CMD    0x05
#define HW_ERR_TX_BUSY        0x07

#define KISS_DEVICE_NAME "Waveshare USB-LoRa KISS"

void kiss_init(void);

// Entry point for frames decoded by the serial layer.
void kiss_frame_received(uint8_t type, const uint8_t* data, size_t size);

/*
    Unsolicited messages emitted by the radio layer.
*/
void kiss_packet_received(int8_t rssi_dbm, int8_t snr_quarter_db, const uint8_t* data, size_t size);
void kiss_packet_transmitted(uint32_t time_on_air);

#endif // KISS_H__
