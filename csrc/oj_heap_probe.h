/* K-5: 取り込んだ Open JTalk のヒープ使用量を、**コードを改変せずに**測る。
 *
 * `cc -include oj_heap_probe.h ...` で当てる。このヘッダが先に `<stdlib.h>` を
 * 読んでから `calloc` / `strdup` / `free` をマクロで差し替えるので、
 * 各 .c が後から `#include <stdlib.h>` してもインクルードガードで無視される。
 *
 * ⚠️ **取り込んだ C は 1 バイトも変えない**（`k4b_vendor.py --check` が守る）。
 *    測るためにソースへ手を入れると、G14a の「ホストと一致」の基準が
 *    自分の改変に依存してしまう。
 *
 * ⚠️ **これはホストでの実測であって ESP32 の値ではない。** ポインタ幅も
 *    アロケータの丸めも違う。ここで測るのは「要求バイト数」で、
 *    実機のヒープ断片化は含まない。
 */
#ifndef OJ_HEAP_PROBE_H
#define OJ_HEAP_PROBE_H

#include <stdlib.h>
#include <string.h>

void *oj_heap_calloc(size_t n, size_t s);
void *oj_heap_malloc(size_t n);
char *oj_heap_strdup(const char *s);
void  oj_heap_free(void *p);

void   oj_heap_reset(void);
size_t oj_heap_peak(void);        /* 同時に生きていた要求バイト数の最大 */
size_t oj_heap_live(void);        /* いま生きている要求バイト数 */
size_t oj_heap_total(void);       /* 累計の要求バイト数 */
size_t oj_heap_n_alloc(void);     /* 確保回数 */
size_t oj_heap_n_unknown(void);   /* 追跡外のポインタを free した回数（0 であるべき） */

#define calloc(n, s) oj_heap_calloc((n), (s))
#define malloc(n)    oj_heap_malloc((n))
#define strdup(s)    oj_heap_strdup((s))
#define free(p)      oj_heap_free((p))

#endif /* OJ_HEAP_PROBE_H */
