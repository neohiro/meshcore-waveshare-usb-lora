#include "radio.h"
#include "pinout.h"
#include "device_id.h"
#include "lora_params.h"
#include "pa_config.h"

#include "sx126x/sx126x.h"

#include <FreeRTOS.h>
#include <task.h>

#include <libopencm3/stm32/rcc.h>
#include <libopencm3/stm32/spi.h>
#include <libopencm3/stm32/exti.h>
#include <libopencm3/cm3/nvic.h>
#include <libopencm3/cm3/scb.h>

#include <string.h>

// The Makefile passes -DWITH_TCXO, but keep a default so this file still builds
// if it is compiled on its own.
#ifndef WITH_TCXO
#define WITH_TCXO 1
#endif

typedef enum {
    // IRQ notifications
    NOTIF_IRQ_RADIO = 0x0001,
    NOTIF_RX_DONE   = 0x0002,
    NOTIF_TX_DONE   = 0x0004,
    NOTIF_TIMEOUT   = 0x0008,

    // Requests processing notifications
    NOTIF_SET_LORA_PARAMS = 0x000100,
    NOTIF_SET_FREQUENCY   = 0x001000,
    NOTIF_SET_TX_POWER    = 0x002000,
    NOTIF_SET_TX          = 0x008000,
    NOTIF_CAD             = 0x020000,
} isr_notification_t;

// The SX1262 LoRa modem reports 0 as 112 kHz, which this build never uses.
#define SX_REG_RXPACKET_STATUS 0x0614

// How long to wait for a CAD to report before giving up. At SF8 and 62.5 kHz a
// CAD is a handful of symbols, so this is generous.
#define CAD_POLL_LIMIT 300

// MeshCore EU/UK narrow preset: 869.618 MHz / SF8 / BW62.5k / CR4/8
#define DEFAULT_FREQ   869618000
#define DEFAULT_POWER  17

// +14dBm and below use the low power PA; above it the high power PA with the
// smallest settings that reach the requested power.
static sx126x_pa_cfg_params_t pa_pwr_cfg = {
    .pa_duty_cycle = 0x02,
    .hp_max = 0x03,
    .device_sel = 0x00, // Always 0 for SX1262
    .pa_lut = 0x01,     // Always 1 for SX1262
};

static sx126x_mod_params_lora_t lora_mod_params = {
    .sf = SX126X_LORA_SF8,
    .bw = SX126X_LORA_BW_062,
    .cr = SX126X_LORA_CR_4_8,
    .ldro = 0,
};
static sx126x_pkt_params_lora_t lora_pkt_params = {
    .preamble_len_in_symb = 16,
    .header_type = SX126X_LORA_PKT_EXPLICIT,
    .pld_len_in_bytes = 255,
    .crc_is_on = true,
    .invert_iq_is_on = false,
};

static uint8_t lora_sync_word = MESHCORE_SYNC_WORD;
static uint8_t rx_boosted = 1;
static int8_t tx_power = DEFAULT_POWER;
static sx126x_ramp_time_t tx_ramp_time = SX126X_RAMP_3400_US;

static uint32_t frequency = DEFAULT_FREQ;

// Custom fallback mode with continuous RX activation after TX
#define SX126X_FALLBACK_STDBY_XOSC_RX   0x31

static uint8_t fallback_mode = SX126X_FALLBACK_STDBY_XOSC_RX;

static int16_t continuous_rssi = -180;

static sx126x_standby_cfg_t standby_mode = SX126X_STANDBY_CFG_RC;

static uint8_t csma_persistence = 63;
static uint8_t csma_slot_time = 10;   // 10 ms units
static uint8_t csma_tx_delay = 50;    // 10 ms units
static bool csma_full_duplex = false;

static uint32_t stat_rx_count = 0;
static uint32_t stat_tx_count = 0;
static uint32_t stat_rx_errors = 0;

// xorshift32, seeded from the MCU's unique device ID on first use.
static uint32_t rnd_state = 0;

static void set_antenna_to_rx()
{
    gpio_set(LORA_RF_SW_PORT, LORA_RF_SW_PIN);
}

static void set_antenna_to_tx()
{
    gpio_clear(LORA_RF_SW_PORT, LORA_RF_SW_PIN);
}

static uint32_t rnd_next(void)
{
    if (rnd_state == 0) {
        rnd_state = device_id_seed();
    }

    rnd_state ^= rnd_state << 13;
    rnd_state ^= rnd_state >> 17;
    rnd_state ^= rnd_state << 5;
    return rnd_state;
}

static void apply_pa_config(int8_t power)
{
    pa_config_for(power, &pa_pwr_cfg.pa_duty_cycle, &pa_pwr_cfg.hp_max);
    tx_power = power;
}

static void update_leds(sx126x_chip_modes_t mode)
{
    if (mode == SX126X_CHIP_MODE_RX) {
        gpio_clear(LED_RXD_PORT, LED_RXD_PIN);
        gpio_set(LED_TXD_PORT, LED_TXD_PIN);
    } else if (mode == SX126X_CHIP_MODE_TX) {
        gpio_set(LED_RXD_PORT, LED_RXD_PIN);
        gpio_clear(LED_TXD_PORT, LED_TXD_PIN);
    } else {
        gpio_set(LED_RXD_PORT, LED_RXD_PIN);
        gpio_set(LED_TXD_PORT, LED_TXD_PIN);
    }
}

static radio_handler_t* handler = NULL;
static TaskHandle_t xRadioIsrTask;

// Synchronous CAD request/response state. Only the radio task may drive the
// SX126x, so a caller asks and then blocks until the radio task answers.
static TaskHandle_t xCadRequester = NULL;
static volatile bool cad_pending = false;
static bool cad_result = false;

static uint8_t tx_buffer[255];
static size_t tx_buffer_size = 0;
static bool transmitting = false;

void exti0_isr(void) {
    BaseType_t xHigherPriorityTaskWoken = pdFALSE;
    exti_reset_request(EXTI0);
    xTaskNotifyFromISR(xRadioIsrTask, NOTIF_IRQ_RADIO, eSetBits, &xHigherPriorityTaskWoken);
    portYIELD_FROM_ISR(xHigherPriorityTaskWoken);
}

// Runs a CAD and restores continuous reception. Any IRQ raised while the CAD is
// running is reported through the returned bits so the caller does not lose a
// packet that landed mid-CAD.
static bool run_cad(uint32_t* irq_carry)
{
    bool busy = false;
    bool completed = false;

    sx126x_irq_mask_t irq;

    sx126x_set_standby(NULL, SX126X_STANDBY_CFG_RC);
    sx126x_clear_irq_status(NULL, SX126X_IRQ_CAD_DONE | SX126X_IRQ_CAD_DETECTED);
    sx126x_set_cad(NULL);

    for (int i = 0; i < CAD_POLL_LIMIT; i++) {
        sx126x_get_irq_status(NULL, &irq);

        if ((irq & SX126X_IRQ_CAD_DONE) != 0) {
            completed = true;
            busy = (irq & SX126X_IRQ_CAD_DETECTED) != 0;
            break;
        }

        // CAD takes a few symbols; yield rather than spin on the bus.
        vTaskDelay(pdMS_TO_TICKS(1));
    }

    // Read the IRQ status once more and hand anything that arrived while the
    // radio was in standby to the caller. Clearing everything here would throw
    // away a packet received during the back-off.
    sx126x_get_irq_status(NULL, &irq);

    if ((irq & SX126X_IRQ_RX_DONE) != 0) {
        *irq_carry |= NOTIF_RX_DONE;
    }
    if ((irq & SX126X_IRQ_TX_DONE) != 0) {
        *irq_carry |= NOTIF_TX_DONE;
    }
    if ((irq & SX126X_IRQ_TIMEOUT) != 0) {
        *irq_carry |= NOTIF_TIMEOUT;
    }

    if (completed) {
        sx126x_clear_irq_status(NULL, SX126X_IRQ_CAD_DONE | SX126X_IRQ_CAD_DETECTED);
    }

    // Resume listening.
    sx126x_set_rx_with_timeout_in_rtc_step(NULL, SX126X_RX_CONTINUOUS);
    set_antenna_to_rx();

    return busy;
}

static void radio_isr_task(void* args __attribute__((unused)))
{
    static uint8_t rx_buffer[255];
    uint8_t rx_buffer_size = 0;

    uint32_t ulNotifiedValue = 0;

    sx126x_reset(NULL);
    sx126x_cfg_tx_clamp(NULL);
    sx126x_set_reg_mode(NULL, SX126X_REG_MODE_LDO);
    sx126x_set_dio2_as_rf_sw_ctrl(NULL, false);
    sx126x_set_standby(NULL, SX126X_STANDBY_CFG_RC);

    sx126x_set_pkt_type(NULL, SX126X_PKT_TYPE_LORA);
    sx126x_set_rf_freq(NULL, frequency);

    sx126x_set_pa_cfg(NULL, &pa_pwr_cfg);
    sx126x_set_tx_params(NULL, tx_power, tx_ramp_time);

    sx126x_set_rx_tx_fallback_mode(NULL, SX126X_FALLBACK_STDBY_RC);
    sx126x_cfg_rx_boosted(NULL, rx_boosted);

    sx126x_set_lora_mod_params(NULL, &lora_mod_params);
    sx126x_set_lora_pkt_params(NULL, &lora_pkt_params);
    sx126x_set_lora_sync_word(NULL, lora_sync_word);

    sx126x_set_dio_irq_params(
        NULL,
        SX126X_IRQ_ALL,
        SX126X_IRQ_TX_DONE | SX126X_IRQ_RX_DONE | SX126X_IRQ_TIMEOUT,
        SX126X_IRQ_NONE,
        SX126X_IRQ_NONE);

    sx126x_clear_irq_status(NULL, SX126X_IRQ_ALL);

#if WITH_TCXO
    // Power the TCXO from DIO3 at 1.7 V. Only correct for the TCXO variant of
    // the board: the -B (XTAL) variant has a 32 MHz crystal driving the
    // synthesizer, and driving DIO3 there is the wrong configuration.
    sx126x_set_dio3_as_tcxo_ctrl(NULL, SX126X_TCXO_CTRL_1_7V, 5000);
#endif

    // Switch antenna to RX
    set_antenna_to_rx();

    // CAD parameters, valid for the current SF/BW.
    sx126x_cad_params_t cad_params = {
        .cad_symb_nb = SX126X_CAD_08_SYMB,
        .cad_detect_peak = 22,
        .cad_detect_min = 10,
        .cad_exit_mode = SX126X_CAD_ONLY,
        .cad_timeout = 0
    };
    sx126x_set_cad_params(NULL, &cad_params);

    // Start out listening.
    sx126x_set_rx_with_timeout_in_rtc_step(NULL, SX126X_RX_CONTINUOUS);

    for (;;) {
        sx126x_chip_status_t chip_status;
        sx126x_get_status(NULL, &chip_status);

        update_leds(chip_status.chip_mode);

        // Measure RSSI in RX mode
        if (chip_status.chip_mode == SX126X_CHIP_MODE_RX) {
            sx126x_get_rssi_inst(NULL, &continuous_rssi);
        }

        // Wait for notifications
        if (xTaskNotifyWait(0,                // bits to clear on entry
                            0xFFFFFFFF,       // bits to clear on exit
                            &ulNotifiedValue, // Notified value pass out in
                            pdMS_TO_TICKS(100)) == pdFALSE)
        {
            continue;
        }

        if (ulNotifiedValue & NOTIF_IRQ_RADIO) {
            sx126x_irq_mask_t irq_mask;
            sx126x_get_and_clear_irq_status(NULL, &irq_mask);

            if ((irq_mask & SX126X_IRQ_RX_DONE) == SX126X_IRQ_RX_DONE) {
                ulNotifiedValue |= NOTIF_RX_DONE;
            }

            if ((irq_mask & SX126X_IRQ_TX_DONE) == SX126X_IRQ_TX_DONE) {
                ulNotifiedValue |= NOTIF_TX_DONE;
            }

            if ((irq_mask & SX126X_IRQ_TIMEOUT) == SX126X_IRQ_TIMEOUT) {
                ulNotifiedValue |= NOTIF_TIMEOUT;
            }
        }

        if (ulNotifiedValue & NOTIF_RX_DONE) {
            sx126x_rx_buffer_status_t rx_buffer_status;
            int8_t rssi_dbm = 0;
            int8_t snr_quarter_db = 0;
            int8_t signal_rssi_dbm = 0;

            sx126x_get_rx_buffer_status(NULL, &rx_buffer_status);

            if (rx_buffer_status.pld_len_in_bytes > 0) {
                radio_get_packet_status(&rssi_dbm, &snr_quarter_db, &signal_rssi_dbm);

                sx126x_read_buffer(NULL, rx_buffer_status.buffer_start_pointer, rx_buffer,
                                   rx_buffer_status.pld_len_in_bytes);
                rx_buffer_size = rx_buffer_status.pld_len_in_bytes;

                stat_rx_count++;

                if (handler != NULL && handler->packet_received != NULL) {
                    handler->packet_received(rssi_dbm, snr_quarter_db, rx_buffer, rx_buffer_size);
                }
            } else {
                stat_rx_errors++;
            }
        }

        if (ulNotifiedValue & NOTIF_TX_DONE) {
            transmitting = false;

            set_antenna_to_rx();

            if (fallback_mode == SX126X_FALLBACK_STDBY_XOSC_RX) {
                sx126x_set_rx_with_timeout_in_rtc_step(NULL, SX126X_RX_CONTINUOUS);
            }

            stat_tx_count++;

            if (handler != NULL && handler->packet_transmitted != NULL) {
                uint32_t time_on_air = sx126x_get_lora_time_on_air_in_ms(&lora_pkt_params, &lora_mod_params);
                handler->packet_transmitted(time_on_air);
            }
        }

        if (ulNotifiedValue & NOTIF_TIMEOUT) {
            if (transmitting) {
                set_antenna_to_rx();

                if (fallback_mode == SX126X_FALLBACK_STDBY_XOSC_RX) {
                    sx126x_set_rx_with_timeout_in_rtc_step(NULL, SX126X_RX_CONTINUOUS);
                }

                transmitting = false;
            }
        }

        if (ulNotifiedValue & NOTIF_SET_LORA_PARAMS) {
            sx126x_set_lora_mod_params(NULL, &lora_mod_params);

            sx126x_cad_params_t cad = {
                .cad_symb_nb = SX126X_CAD_08_SYMB,
                .cad_detect_peak = 22,
                .cad_detect_min = 10,
                .cad_exit_mode = SX126X_CAD_ONLY,
                .cad_timeout = 0
            };
            sx126x_set_cad_params(NULL, &cad);
        }

        if (ulNotifiedValue & NOTIF_SET_FREQUENCY) {
            sx126x_set_rf_freq(NULL, frequency);
        }

        if (ulNotifiedValue & NOTIF_SET_TX_POWER) {
            sx126x_set_pa_cfg(NULL, &pa_pwr_cfg);
            sx126x_set_tx_params(NULL, tx_power, tx_ramp_time);
        }

        if (ulNotifiedValue & NOTIF_CAD) {
            uint32_t carry = 0;
            bool busy;

            if (transmitting) {
                // A CAD switches the radio to standby and clears the IRQ
                // status. Doing that mid-transmission would destroy the
                // TX_DONE IRQ, leaving `transmitting` stuck true and making
                // the modem refuse every later transmission. A radio that is
                // transmitting is busy by definition, so answer without
                // touching it.
                busy = true;
            } else {
                busy = run_cad(&carry);
                ulNotifiedValue |= carry;
            }

            if (cad_pending) {
                cad_pending = false;
                cad_result = busy;

                if (xCadRequester != NULL) {
                    xTaskNotify(xCadRequester, 0, eNoAction);
                }
            }
        }

        if (ulNotifiedValue & NOTIF_SET_TX) {
            bool go = true;

            if (!csma_full_duplex) {
                uint32_t carry = 0;
                int attempt = 0;
                bool busy = run_cad(&carry);

                ulNotifiedValue |= carry;

                while (busy && attempt < 20) {
                    attempt++;
                    vTaskDelay(pdMS_TO_TICKS(csma_slot_time * 10));

                    carry = 0;
                    busy = run_cad(&carry);
                    ulNotifiedValue |= carry;
                }

                // p-persistent: transmit only with probability (P + 1) / 256
                if (!busy && (int8_t)((rnd_next() & 0xFF) <= csma_persistence)) {
                    vTaskDelay(pdMS_TO_TICKS(csma_tx_delay * 10));
                } else {
                    go = false;
                }
            }

            if (go) {
                sx126x_write_buffer(NULL, 0, tx_buffer, tx_buffer_size);

                lora_pkt_params.pld_len_in_bytes = tx_buffer_size;
                sx126x_set_lora_pkt_params(NULL, &lora_pkt_params);

                set_antenna_to_tx();
                sx126x_set_tx(NULL, 0);
            } else {
                transmitting = false;
                set_antenna_to_rx();
                sx126x_set_rx_with_timeout_in_rtc_step(NULL, SX126X_RX_CONTINUOUS);

                if (handler != NULL && handler->packet_transmitted != NULL) {
                    handler->packet_transmitted(0);
                }
            }
        }
    }
}

void radio_init(radio_handler_t* h)
{
    // SX1262 DIO1 interrupt
    exti_select_source(LORA_DIO1_EXTI0, LORA_DIO1_PORT);
    exti_set_trigger(LORA_DIO1_EXTI0, EXTI_TRIGGER_RISING);
    exti_enable_request(LORA_DIO1_EXTI0);
    nvic_set_priority(LORA_DIO1_IRQ, LORA_IRQ_PRIORITY);
    nvic_enable_irq(LORA_DIO1_IRQ);

    // SPI communication with SX126X
    rcc_periph_clock_enable(LORA_RCC_SPI);
    gpio_set_mode(GPIOB,
                  GPIO_MODE_OUTPUT_50_MHZ,
                  GPIO_CNF_OUTPUT_ALTFN_PUSHPULL,
                  LORA_SPI_NSS | LORA_SPI_SCK | LORA_SPI_MOSI);

    gpio_set_mode(GPIOB, GPIO_MODE_INPUT, GPIO_CNF_INPUT_FLOAT, LORA_SPI_MISO);

    spi_init_master(LORA_SPI,
                    SPI_CR1_BAUDRATE_FPCLK_DIV_128,
                    SPI_CR1_CPOL_CLK_TO_0_WHEN_IDLE,
                    SPI_CR1_CPHA_CLK_TRANSITION_1,
                    SPI_CR1_DFF_8BIT,
                    SPI_CR1_MSBFIRST);

    spi_disable_software_slave_management(LORA_SPI);
    spi_enable_ss_output(LORA_SPI);

    tx_buffer_size = 0;
    transmitting = false;

    handler = h;

    // Turn LEDs off
    gpio_set(LED_TXD_PORT, LED_TXD_PIN);
    gpio_set(LED_RXD_PORT, LED_RXD_PIN);

    radio_set_meshcore_packet_params();

    xTaskCreate(radio_isr_task, "RADIO_ISR", 256, NULL, configMAX_PRIORITIES - 1, &xRadioIsrTask);
}

void radio_get_lora_params(radio_lora_params_t* params)
{
    if (params == NULL)
        return;

    params->spreading_factor = lora_mod_params.sf;
    params->bandwidth = lora_mod_params.bw;
    params->coding_rate = lora_mod_params.cr;
    params->low_data_rate = lora_mod_params.ldro;
}

void radio_set_lora_params(const radio_lora_params_t* params)
{
    if (params == NULL)
        return;

    if (params->spreading_factor >= SX126X_LORA_SF5 && params->spreading_factor <= SX126X_LORA_SF12)
        lora_mod_params.sf = params->spreading_factor;

    // SX126x bandwidth enumerants run 0..10 with 7 unused, so an unsigned
    // range check against zero would be vacuous.
    if (params->bandwidth <= SX126X_LORA_BW_041 &&
        params->bandwidth != 0x07) {
            lora_mod_params.bw = params->bandwidth;
        }

    if (params->coding_rate >= SX126X_LORA_CR_4_5 && params->coding_rate <= SX126X_LORA_CR_4_8)
        lora_mod_params.cr = params->coding_rate;

    // RadioLib derives LDRO from the symbol duration, not from SF alone, so
    // this has to be recomputed whenever either changes.
    lora_mod_params.ldro = lora_ldro_for(lora_mod_params.sf, lora_mod_params.bw);

    xTaskNotify(xRadioIsrTask, NOTIF_SET_LORA_PARAMS, eSetBits);
}

uint32_t radio_get_frequency()
{
    return frequency;
}

void radio_set_frequency(uint32_t f)
{
    frequency = f;
    xTaskNotify(xRadioIsrTask, NOTIF_SET_FREQUENCY, eSetBits);
}

int16_t radio_get_continuous_rssi()
{
    return continuous_rssi;
}

int8_t radio_get_tx_power()
{
    return tx_power;
}

void radio_set_tx_power(int8_t power)
{
    apply_pa_config(power);
    xTaskNotify(xRadioIsrTask, NOTIF_SET_TX_POWER, eSetBits);
}

void radio_set_meshcore_packet_params(void)
{
    lora_pkt_params.preamble_len_in_symb = 16;
    lora_pkt_params.header_type = SX126X_LORA_PKT_EXPLICIT;
    lora_pkt_params.pld_len_in_bytes = 255;
    lora_pkt_params.crc_is_on = true;
    lora_pkt_params.invert_iq_is_on = false;
    lora_sync_word = MESHCORE_SYNC_WORD;
}

bool radio_is_tx_active()
{
    return transmitting;
}

void radio_set_tx(const uint8_t* payload, size_t payload_size)
{
    if (transmitting || payload == NULL) {
        return;
    }

    transmitting = true;

    // Kept as a size_t so this clamp is real: callers pass a size_t and the
    // LoRa payload length field is a single byte.
    tx_buffer_size = payload_size;
    if (tx_buffer_size > sizeof(tx_buffer))
        tx_buffer_size = sizeof(tx_buffer);

    memcpy(tx_buffer, payload, tx_buffer_size);
    xTaskNotify(xRadioIsrTask, NOTIF_SET_TX, eSetBits);
}

uint8_t radio_get_standby()
{
    return standby_mode;
}

void radio_set_standby(uint8_t mode)
{
    if (mode == SX126X_STANDBY_CFG_RC || mode == SX126X_STANDBY_CFG_XOSC)
        standby_mode = mode;
}

void radio_set_csma_params(uint8_t persistence, uint8_t slot_time, uint8_t tx_delay, bool full_duplex)
{
    csma_persistence = persistence;
    csma_slot_time = slot_time;
    csma_tx_delay = tx_delay;
    csma_full_duplex = full_duplex;
}

bool radio_is_channel_busy(void)
{
    if (xRadioIsrTask == NULL || xRadioIsrTask == xTaskGetCurrentTaskHandle()) {
        return false;
    }

    cad_result = false;
    cad_pending = true;
    xCadRequester = xTaskGetCurrentTaskHandle();

    xTaskNotify(xRadioIsrTask, NOTIF_CAD, eSetBits);

    // Block until the radio task answers. Bounded so a wedged radio cannot
    // hang the KISS command handler forever.
    ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(2000));

    xCadRequester = NULL;

    return cad_result;
}

uint32_t radio_get_airtime(uint8_t payload_len)
{
    // Operate on a copy: lora_pkt_params belongs to the radio task, which may
    // be setting the payload length for a transmission at the same time.
    sx126x_pkt_params_lora_t params = lora_pkt_params;

    params.pld_len_in_bytes = payload_len;

    return sx126x_get_lora_time_on_air_in_ms(&params, &lora_mod_params);
}

void radio_get_packet_status(int8_t* rssi_dbm, int8_t* snr_quarter_db, int8_t* signal_rssi_dbm)
{
    uint8_t raw[4] = { 0 };

    sx126x_read_register(NULL, SX_REG_RXPACKET_STATUS, raw, 4);

    // Byte 0 is the packet status, byte 1 the signal RSSI, byte 2 the SNR
    // (signed, quarter dB steps) and byte 3 the signal RSSI after despreading.
    if (rssi_dbm != NULL)
        *rssi_dbm = (int8_t)(-(int8_t)raw[1] >> 1);

    if (snr_quarter_db != NULL)
        *snr_quarter_db = (int8_t)raw[2];

    if (signal_rssi_dbm != NULL)
        *signal_rssi_dbm = (int8_t)(-(int8_t)raw[3] >> 1);
}

void radio_get_stats(uint32_t* rx_count, uint32_t* tx_count, uint32_t* rx_errors)
{
    if (rx_count != NULL)
        *rx_count = stat_rx_count;

    if (tx_count != NULL)
        *tx_count = stat_tx_count;

    if (rx_errors != NULL)
        *rx_errors = stat_rx_errors;
}

void radio_reboot(void)
{
    SCB_AIRCR = 0x05FA0004;
}
