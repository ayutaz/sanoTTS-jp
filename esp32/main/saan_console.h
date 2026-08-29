/* シリアルコンソールからの 1 行入力（かな中間表現）。
 *
 * 行編集そのものは `csrc/line.c` のステートマシン（ホストでゲート済み。
 * `make -C csrc line`）。ここがやるのは **バイトの取り込みとエコーだけ**。
 *
 * ⚠️ **どのポートに挿すかはコンソールの設定で決まる。**
 *    既定 (`CONFIG_ESP_CONSOLE_UART_DEFAULT`) では **UART0**、つまり
 *    DevKit の「UART」と書いてある方の USB ポート。ESP32-S3 の DevKit には
 *    USB ポートが 2 つあり、**「USB」側（native USB-Serial-JTAG）に挿すと
 *    ログは見えるのに入力が届かない**。`idf.py menuconfig` で
 *    `Channel for console output` を `USB Serial/JTAG` にすればそちらでも使える
 *    （このファイルは両方に対応してある）。
 *
 * ⚠️ **ホスト stub ではコンパイルしない**（IDF の driver が要る）。
 *    main.c 側は `SAAN_INTERACTIVE` で切り分ける。
 */
#ifndef SAAN_CONSOLE_H
#define SAAN_CONSOLE_H

#include <stdbool.h>
#include <stddef.h>

/* 対話ループを持つか。**ESP-IDF でビルドしたときだけ 1。**
 * ホスト stub (`scripts/check_esp32_template.sh` のゲート 8) は app_main() を
 * 同期実行して戻り値を突き合わせるので、対話ループに入ると**返ってこない**。 */
#ifndef SAAN_INTERACTIVE
#  ifdef ESP_PLATFORM
#    define SAAN_INTERACTIVE 1
#  else
#    define SAAN_INTERACTIVE 0
#  endif
#endif

/* 入力バッファ。**上限を超えたら切り詰めずに行ごと拒否する**（line.c の overflow）。
 * 512 B は、かな 1 文字 3 B で約 170 文字。ids の上限 (SAAN_MAX_IDS) の方が
 * 先に効くので、ここは「異常に長い貼り付けを止める」ための枠。
 * ⚠️ `#if SAAN_INTERACTIVE` の**外**に置く。main.c が対話の有無に関係なく
 *    ids バッファの大きさをこれから決めるため。 */
#define SAAN_CONSOLE_LINE_MAX 512

#if SAAN_INTERACTIVE

bool saan_console_init(void);

/* 1 行読む。**プロンプトの表示とエコーもここでやる。**
 *   戻り値 >= 0 : 行のバイト数。`*out` に NUL 終端の行が入る
 *   戻り値 -2   : 入力が長すぎた（`*out` は使ってはいけない）
 *   戻り値 -1   : 読み取りエラー
 * ⚠️ 空行 (0) は「何も打たずに Enter」。呼び出し側で弾くこと。
 *
 * ⚠️ **バッファは呼び出し側から渡さない。** 行編集の状態（特に CRLF の
 *    「次の LF を吸う」フラグ）は**行をまたいで持ち越す必要がある**ので、
 *    バッファと状態はこのモジュールが 1 組だけ持つ。呼び出しごとに
 *    `saan_line_reset()` すると、CRLF を送る端末で**発話のたびに空行が 1 回**入る
 *    （QEMU で実際に踏んだ）。`*out` は次に readline を呼ぶまで有効。 */
int saan_console_readline(const char **out);

#define SAAN_CONSOLE_TOO_LONG (-2)
#define SAAN_CONSOLE_ERROR    (-1)

#endif /* SAAN_INTERACTIVE */
#endif /* SAAN_CONSOLE_H */
