#include "saan_console.h"

#if SAAN_INTERACTIVE

#include <string.h>

#include "esp_log.h"
#include "sdkconfig.h"

#include "line.h"

static const char *TAG = "saan_con";

/* ドライバの RX リング。**行の上限より大きくする**（M-84）。
 *
 * ⚠️ **既定の 256 B では 300 B のかな行を一度に貼ると途中で欠ける。**
 *    実機で踏んだ: 長い行を送ると先頭だけが届き、**短い列として普通に喋る**ので
 *    音では気づけない（`saan_g2p` は途中で切れた列も妥当な中間表現として通す）。
 * ⚠️ **行編集側の上限を上げるだけでは直らない。** 溢れるのはドライバの
 *    リングで、`saan_line` に届く前に落ちている。両方が要る。 */
#define SAAN_CONSOLE_RX_RING (2 * SAAN_CONSOLE_LINE_MAX)

/* --- バイトの出し入れ（コンソールの種類で切り替える）----------------------
 *
 * ⚠️ **VFS (`stdin` / `fgetc`) を使わない。** VFS 経由の名前は IDF の版で
 *    変わっている（v5.3 で `esp_vfs_dev_uart_use_driver` →
 *    `uart_vfs_dev_use_driver`）。ドライバの API を直接叩けばその揺れを踏まない。
 * ⚠️ **エコーもここから出す。** ESP_LOG と同じ FIFO に出るが、合成タスク 1 本
 *    しか喋らないので混ざらない。 */

#if defined(CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG)

#include "driver/usb_serial_jtag.h"

static bool port_init(void) {
    usb_serial_jtag_driver_config_t cfg = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    cfg.rx_buffer_size = SAAN_CONSOLE_RX_RING;   /* 既定 256 B では長い行が欠ける（M-84） */
    esp_err_t err = usb_serial_jtag_driver_install(&cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "usb_serial_jtag_driver_install: %s", esp_err_to_name(err));
        return false;
    }
    ESP_LOGI(TAG, "入力: USB Serial/JTAG（DevKit の「USB」ポート）/ RX リング %d B",
             (int)SAAN_CONSOLE_RX_RING);
    return true;
}

static int port_read1(unsigned char *c, uint32_t timeout_ms) {
    return usb_serial_jtag_read_bytes(c, 1, pdMS_TO_TICKS(timeout_ms)) == 1 ? 1 : 0;
}

static void port_write(const char *s, size_t n) {
    usb_serial_jtag_write_bytes(s, n, portMAX_DELAY);
}

#elif defined(CONFIG_ESP_CONSOLE_UART_DEFAULT) || defined(CONFIG_ESP_CONSOLE_UART_CUSTOM)

#include "driver/uart.h"

#define CON_UART CONFIG_ESP_CONSOLE_UART_NUM

static bool port_init(void) {
    /* ⚠️ **TX バッファは 0 にする。** 0 なら `uart_write_bytes` は割り込みを
     *    使わず FIFO へ直接書くので、ESP_LOG（ドライバを通らない経路）と
     *    同じ FIFO を取り合っても順序が壊れない。 */
    /* ⚠️ RX も既定の 256 B では長い行が欠ける（USB Serial/JTAG と同じ理由。M-84）。 */
    esp_err_t err = uart_driver_install(CON_UART, SAAN_CONSOLE_RX_RING, 0, 0, NULL, 0);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "uart_driver_install(UART%d): %s", (int)CON_UART,
                 esp_err_to_name(err));
        return false;
    }
    ESP_LOGI(TAG, "入力: UART%d @ %d baud（DevKit の「UART」ポート）/ RX リング %d B",
             (int)CON_UART, (int)CONFIG_ESP_CONSOLE_UART_BAUDRATE,
             (int)SAAN_CONSOLE_RX_RING);
    return true;
}

static int port_read1(unsigned char *c, uint32_t timeout_ms) {
    return uart_read_bytes(CON_UART, c, 1, pdMS_TO_TICKS(timeout_ms)) == 1 ? 1 : 0;
}

static void port_write(const char *s, size_t n) {
    uart_write_bytes(CON_UART, s, n);
}

#else
#error "コンソールが無効（CONFIG_ESP_CONSOLE_NONE）。対話入力は使えない。 \
menuconfig で UART か USB Serial/JTAG を選ぶか、SAAN_INTERACTIVE=0 でビルドすること"
#endif

/* --- 表示 ----------------------------------------------------------------- */

#define PROMPT "かな> "

static void put(const char *s) { port_write(s, strlen(s)); }

/* ⚠️ **BS のときは 1 文字ぶん消すのではなく行ごと書き直す。**
 *    ひらがなは端末上で 2 セル幅なので `"\b \b"` では消し残る
 *    （何が残っているのか分からなくなるのが一番困る）。 */
static void redraw(const saan_line *ln) {
    put("\r\x1b[K" PROMPT);
    if (ln->len > 0) port_write(ln->buf, ln->len);
}

/* ⚠️ **行編集の状態は 1 組だけ持ち、行をまたいで持ち越す。**
 *
 * かつて readline の中で `saan_line ln;` を毎回作って `saan_line_reset()` して
 * いたが、それだと **CRLF の「次の LF を吸う」フラグ (`cr`) が毎回消える**。
 * `\r` で行が完成 → 合成 → 次の readline で reset → 遅れて届いた `\n` が
 * **空行としてもう 1 行**になる。QEMU で実際に踏んだ（発話のたびに
 * 「空行。かな中間表現を入力すること」が 1 回出る）。
 *
 * 行の中身は `saan_line_feed()` が DONE の次のバイトで自動的に消すので、
 * **ここで reset してはいけない**（init の 1 回だけ）。
 * この危険は `make -C csrc line` の G9 が「reset するとこうなる」として固定してある。 */
static char      s_buf[SAAN_CONSOLE_LINE_MAX];
static saan_line s_ln;

bool saan_console_init(void) {
    if (!port_init()) return false;
    saan_line_reset(&s_ln, s_buf, sizeof s_buf);
    return true;
}

void saan_console_prompt(void) {
    put("\r\n" PROMPT);
}

/* ⚠️ **タイムアウトで戻る。** 行の途中でも戻り、状態は s_ln に残る。
 *    呼び出し側は PENDING のあいだにタッチなど別の入力を見られる。
 *    プロンプトはここでは出さない（saan_console_prompt() を行ごとに 1 回）。
 * ⚠️ read が 0 バイトで返るのを「エラー」と区別できない（どちらも戻り値 0）ので、
 *    タイムアウトは常に PENDING として返す。本物の切断は次の書き込みで分かる。 */
int saan_console_poll(const char **out, uint32_t timeout_ms) {
    *out = s_buf;

    for (;;) {
        unsigned char c;
        if (!port_read1(&c, timeout_ms)) return SAAN_CONSOLE_PENDING;

        /* ⚠️ **エコーの要否を自分で判定しない。** かつて feed の前後で `len` が
         *    増えたかで判定していたが、DONE の次のバイトでは中身が自動的に消えるので
         *    `before` が前の行の長さになり、**行の先頭 1 文字がエコーされなかった**
         *    （QEMU で実際に踏んだ）。合成される列は正しいままなので**音では
         *    気づけない**。判定は状態機械の返す SAAN_LINE_ECHO に任せる。 */
        switch (saan_line_feed(&s_ln, c)) {
        case SAAN_LINE_DONE:
            put("\r\n");
            /* ⚠️ **溢れた行は使わせない。** 切り詰めた先頭だけを喋ると、
             *    「端末とホストで同じ列」という入力仕様が黙って崩れる。 */
            return s_ln.overflow ? SAAN_CONSOLE_TOO_LONG : (int)s_ln.len;
        case SAAN_LINE_EDIT:
            redraw(&s_ln);
            break;
        case SAAN_LINE_ECHO:
            port_write((const char *)&c, 1);
            break;
        case SAAN_LINE_MORE:
            /* 捨てたバイト（ESC の途中・制御文字・溢れ）。**エコーもしない** —
             * 画面が止まることが「もう入っていない」の合図になる。 */
            break;
        }
    }
}

#endif /* SAAN_INTERACTIVE */
