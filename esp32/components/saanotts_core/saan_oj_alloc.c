/* T10(b): 取り込んだ Open JTalk の一時ヒープを PSRAM に向ける（ESP32 側の実装）。
 *
 * ⚠️ **このファイルには `-include saan_oj_alloc.h` を当てない。**
 *    当てると calloc/free がここでも置き換わって無限再帰になる。
 *    当てるのは `csrc/openjtalk の .c 全部` だけ（component の CMakeLists で
 *    set_source_files_properties していて、このファイルは対象外）。
 *
 * ⚠️ **これは「NULL 事故を構造的に防ぐ」ものではない**（審査で判明）。
 *    M5 の構成は `CONFIG_SPIRAM_USE_MALLOC=y` なので、内部が尽きれば
 *    素の malloc でも PSRAM に落ちる。確保に失敗したときの Open JTalk の
 *    壊れ方（`mecab2njd` は WARNING を出して途中 return、`make_label` は
 *    size=0。**エラーは返らない**）はここでは変わらない。
 *
 * ⚠️ **それでも意味はある。** 同じ構成の生成 sdkconfig は
 *    `CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=16384`（実測）で、
 *    **16 KB 以下の malloc は内部 DRAM を先に試す**。Open JTalk の確保は
 *    NJDNode / JPCommonNode / ラベル文字列で全部これより小さいので、
 *    素の malloc では 1 文ぶん（ホストで最大 97,325 B。K-5）が**まるごと
 *    内部 DRAM に載る**。ここでやっているのは
 *    **内部 DRAM を先に食わせない**ことだけ（headroom の確保）。
 *
 * ⚠️ **PSRAM が無い構成（DevKit の QEMU ビルド）でも動く。**
 *    その板では `heap_caps_*(MALLOC_CAP_SPIRAM)` が NULL を返すので
 *    内部 DRAM に落ちる = 従来と同じ挙動。
 */
#include <stddef.h>
#include <string.h>

#include "sdkconfig.h"
#include "esp_heap_caps.h"

/* ⚠️ **PSRAM が無い板で毎回 SPIRAM を試すのは無駄。** 1 回だけ聞いて覚える。
 *    0 = 未確認 / 1 = PSRAM ヒープあり / -1 = 無し */
static int s_have_psram;

static int have_psram(void) {
    if (s_have_psram == 0)
        s_have_psram = (heap_caps_get_total_size(MALLOC_CAP_SPIRAM) > 0) ? 1 : -1;
    return s_have_psram > 0;
}

void *saan_oj_calloc(size_t n, size_t s) {
    if (have_psram()) {
        void *p = heap_caps_calloc(n, s, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (p) return p;
    }
    return heap_caps_calloc(n, s, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
}

void *saan_oj_malloc(size_t n) {
    if (have_psram()) {
        void *p = heap_caps_malloc(n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (p) return p;
    }
    return heap_caps_malloc(n, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
}

char *saan_oj_strdup(const char *s) {
    if (s == NULL) return NULL;
    size_t n = strlen(s) + 1u;
    char *p = (char *)saan_oj_malloc(n);
    if (p) memcpy(p, s, n);
    return p;
}

/* heap_caps_free は内部 DRAM / PSRAM のどちらのポインタでも受ける。 */
void saan_oj_free(void *p) { heap_caps_free(p); }
