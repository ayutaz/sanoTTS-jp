/* T10(b): 取り込んだ Open JTalk の一時ヒープを **コードを改変せずに** 移す。
 *
 * `cc -include oj_heap_psram.h ...` で `csrc/openjtalk の .c 全部` **だけ**に当てる。
 * このヘッダが先に `<stdlib.h>` / `<string.h>` を読んでから
 * `calloc` / `strdup` / `free` をマクロで差し替えるので、各 .c が後から
 * `#include <stdlib.h>` してもインクルードガードで無視される
 * （K-5 の `csrc/oj_heap_probe.h` と同じ手口）。
 *
 * ⚠️ **取り込んだ C は 1 バイトも変えない**（`k4b_vendor.py --check` が守る）。
 *    ここに手を入れると K-6 / K-4b の「ホストと一致」の基準が自分の改変に依存する。
 *
 * ⚠️ **`realloc` は差し替えていない。** 取り込んだ 8 ファイルの実測で
 *    使われているのは calloc 33 / free 74 / strdup 20 だけ
 *    （`cd csrc/openjtalk` して
 *     `grep -ho "\b(calloc|malloc|realloc|strdup|free)\b" *.c | sort | uniq -c`）。
 *    ⚠️ 上流を上げて realloc が入ったら、**このヘッダを通らずに**
 *       素の realloc が呼ばれ、差し替えた calloc のポインタを渡すことになる。
 *       ESP-IDF ではどちらも同じアロケータなので壊れないが、
 *       「PSRAM に置く」という意図は静かに崩れる。
 *
 * 実装（`saan_oj_alloc()` など）は **ターゲット側**が出す:
 *   ESP32 … esp32/components/saanotts_core/oj_heap_psram.c（heap_caps、PSRAM 優先）
 * ホストのゲート（`make -C csrc oj-heap/kanji-e2e/label-ids`）はこのヘッダを当てないので、
 * csrc は移植可能 C99 のまま。
 */
#ifndef SAAN_OJ_ALLOC_H
#define SAAN_OJ_ALLOC_H

#include <stdlib.h>
#include <string.h>

void *saan_oj_calloc(size_t n, size_t s);
void *saan_oj_malloc(size_t n);
char *saan_oj_strdup(const char *s);
void  saan_oj_free(void *p);

#define calloc(n, s) saan_oj_calloc((n), (s))
#define malloc(n)    saan_oj_malloc((n))
#define strdup(s)    saan_oj_strdup((s))
#define free(p)      saan_oj_free((p))

#endif /* SAAN_OJ_ALLOC_H */
