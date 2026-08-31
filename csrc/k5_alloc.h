/* K-5: 取り込んだ Open JTalk のヒープ使用量を、**コードを改変せずに**測る。
 *
 * `cc -include k5_alloc.h ...` で当てる。このヘッダが先に `<stdlib.h>` を
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
#ifndef K5_ALLOC_H
#define K5_ALLOC_H

#include <stdlib.h>
#include <string.h>

void *k5_calloc(size_t n, size_t s);
void *k5_malloc(size_t n);
char *k5_strdup(const char *s);
void  k5_free(void *p);

void   k5_reset(void);
size_t k5_peak(void);        /* 同時に生きていた要求バイト数の最大 */
size_t k5_live(void);        /* いま生きている要求バイト数 */
size_t k5_total(void);       /* 累計の要求バイト数 */
size_t k5_n_alloc(void);     /* 確保回数 */
size_t k5_n_unknown(void);   /* 追跡外のポインタを free した回数（0 であるべき） */

#define calloc(n, s) k5_calloc((n), (s))
#define malloc(n)    k5_malloc((n))
#define strdup(s)    k5_strdup((s))
#define free(p)      k5_free((p))

#endif /* K5_ALLOC_H */
