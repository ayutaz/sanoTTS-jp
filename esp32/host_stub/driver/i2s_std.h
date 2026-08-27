/* ホスト stub — I2S に書いた内容を配列に貯めるだけ。**デバイスには載らない。** */
#ifndef SAAN_STUB_I2S_STD_H
#define SAAN_STUB_I2S_STD_H

#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"
#include "freertos/FreeRTOS.h"

#define I2S_NUM_AUTO       (-1)
#define I2S_GPIO_UNUSED    (-1)
#define I2S_ROLE_MASTER    0
#define I2S_SLOT_MODE_MONO 1
#define I2S_DATA_BIT_WIDTH_16BIT 16

typedef void *i2s_chan_handle_t;
typedef struct { int id; int role; uint32_t dma_desc_num, dma_frame_num; int auto_clear; } i2s_chan_config_t;
typedef struct { uint32_t sample_rate_hz; } i2s_std_clk_config_t;
typedef struct { int data_bit_width, slot_mode; } i2s_std_slot_config_t;
typedef struct {
    int mclk, bclk, ws, dout, din;
    struct { int mclk_inv, bclk_inv, ws_inv; } invert_flags;
} i2s_std_gpio_config_t;
typedef struct {
    i2s_std_clk_config_t clk_cfg;
    i2s_std_slot_config_t slot_cfg;
    i2s_std_gpio_config_t gpio_cfg;
} i2s_std_config_t;

#define I2S_CHANNEL_DEFAULT_CONFIG(num, r) \
    (i2s_chan_config_t){ .id = (num), .role = (r), .dma_desc_num = 6, \
                         .dma_frame_num = 240, .auto_clear = 0 }
#define I2S_STD_CLK_DEFAULT_CONFIG(sr) (i2s_std_clk_config_t){ .sample_rate_hz = (sr) }
#define I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(bw, sm) \
    (i2s_std_slot_config_t){ .data_bit_width = (bw), .slot_mode = (sm) }

esp_err_t i2s_new_channel(const i2s_chan_config_t *cfg, i2s_chan_handle_t *tx,
                          i2s_chan_handle_t *rx);
esp_err_t i2s_channel_init_std_mode(i2s_chan_handle_t h, const i2s_std_config_t *cfg);
esp_err_t i2s_channel_enable(i2s_chan_handle_t h);
esp_err_t i2s_channel_disable(i2s_chan_handle_t h);
esp_err_t i2s_channel_write(i2s_chan_handle_t h, const void *src, size_t size,
                            size_t *written, TickType_t timeout);

/* 貯まった int16 サンプルを取り出す（host_main.c が使う） */
const int16_t *saan_stub_i2s_pcm(size_t *n_samples);

#endif
