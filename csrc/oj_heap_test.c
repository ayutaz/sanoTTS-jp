/* K-5 の受け入れゲート（計測編）。   make -C csrc k5
 *
 * G22  1 文あたりのヒープ・スタックのピークを実測する
 * G23  ラベルバッファ（`MAXBUFLEN`）を詰めた効果を数字で出す
 * G24  陽性対照つき: 詰める前後で**ラベル文字列が bit 一致**する
 *
 * 測る対象は端末が実際に走らせる経路の全部:
 *   mecab2njd → njd_set_pronunciation → njd_rules_before_chaining → njd_set_digit
 *   → njd_set_accent_phrase → njd_set_accent_type → njd_set_unvoiced_vowel
 *   → njd_set_long_vowel → **njd2jpcommon → JPCommon_make_label**
 * （K-2 / K-3 の辞書引きと Viterbi は入っていない。あちらは別途 K-2 で測る）
 *
 * ⚠️ **ホストでの実測であって ESP32 の値ではない。** ポインタ幅もアロケータの
 *    丸めも違う。ここで測るのは要求バイト数と、実際に踏んだスタックの深さ。
 * ⚠️ **取り込んだ C は改変していない。** ヒープは `-include oj_heap_probe.h` の
 *    マクロで、スタックは自前スタックの塗り潰しで測る。
 */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <sys/mman.h>

#include "openjtalk/njd.h"
#include "openjtalk/jpcommon.h"
#include "openjtalk/mecab2njd.h"
#include "openjtalk/njd2jpcommon.h"
#include "njd_rules.h"
#include "oj_heap_probe.h"
#include "openjtalk/njd_set_pronunciation.h"
#include "openjtalk/njd_set_digit.h"
#include "openjtalk/njd_set_accent_phrase.h"
#include "openjtalk/njd_set_accent_type.h"
#include "openjtalk/njd_set_unvoiced_vowel.h"
#include "openjtalk/njd_set_long_vowel.h"

#define MAX_FEAT 1024
#define STACK_BYTES (1u << 20)     /* 1 MiB。足りなければ落ちるので気づく */
#define PAINT 0xA5

static const uint8_t *g;
static uint32_t rd32(void) {
    uint32_t v = (uint32_t)g[0] | ((uint32_t)g[1] << 8)
               | ((uint32_t)g[2] << 16) | ((uint32_t)g[3] << 24);
    g += 4; return v;
}
static char *rdstr(char *out, size_t cap) {
    uint16_t n = (uint16_t)(g[0] | (g[1] << 8)); g += 2;
    size_t k = (n < cap - 1) ? n : cap - 1;
    memcpy(out, g, k); out[k] = 0; g += n;
    return out;
}

/* ---------------------------------------------------------------- 1 文分 */

static char *feat[MAX_FEAT];
static char febuf[MAX_FEAT][1024];
static int  n_feat;

/* このケースで作られたラベルの連結。G24 の突き合わせに使う。 */
static char *label_join;
static size_t label_len;
static int    label_count;

static void run_one(void) {
    NJD njd; JPCommon jpcommon;
    NJD_initialize(&njd);
    JPCommon_initialize(&jpcommon);

    mecab2njd(&njd, feat, n_feat);
    njd_set_pronunciation(&njd);
    njd_rules_before_chaining(&njd);
    njd_set_digit(&njd);
    njd_set_accent_phrase(&njd);
    njd_set_accent_type(&njd);
    njd_set_unvoiced_vowel(&njd);
    njd_set_long_vowel(&njd);
    njd2jpcommon(&jpcommon, &njd);
    JPCommon_make_label(&jpcommon);

    int n = JPCommon_get_label_size(&jpcommon);
    char **fs = JPCommon_get_label_feature(&jpcommon);
    label_count = n;
    label_len = 0;
    for (int i = 0; i < n; i++) label_len += strlen(fs[i]) + 1;
    label_join = (char *)realloc(label_join, label_len + 1);
    size_t o = 0;
    for (int i = 0; i < n; i++) {
        size_t L = strlen(fs[i]);
        memcpy(label_join + o, fs[i], L); o += L;
        label_join[o++] = '\n';
    }
    label_join[o] = 0;

    JPCommon_clear(&jpcommon);
    NJD_clear(&njd);
}

static void *thread_main(void *arg) { (void)arg; run_one(); return NULL; }

/* 塗り潰した自前スタックで走らせ、踏まれた深さを返す。 */
static size_t run_on_painted_stack(uint8_t *stack) {
    memset(stack, PAINT, STACK_BYTES);
    pthread_attr_t at;
    pthread_attr_init(&at);
    pthread_attr_setstack(&at, stack, STACK_BYTES);
    pthread_t th;
    if (pthread_create(&th, &at, thread_main, NULL) != 0) {
        fprintf(stderr, "NG: pthread_create\n"); exit(1);
    }
    pthread_join(th, NULL);
    pthread_attr_destroy(&at);
    /* スタックは下へ伸びる。先頭から塗り残しを探す */
    size_t i = 0;
    while (i < STACK_BYTES && stack[i] == PAINT) i++;
    return STACK_BYTES - i;
}

int main(int argc, char **argv) {
    const char *path = (argc > 1) ? argv[1] : "njd_rules_vectors.bin";
    /* ラベルの出力先。詰める前後の 2 本を作って `diff` するのが G24。 */
    const char *out_path = (argc > 2) ? argv[2] : "oj_labels.txt";
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "NG: ベクタが開けない: %s\n", path); return 1; }
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t *buf = malloc((size_t)sz);
    if (!buf || fread(buf, 1, (size_t)sz, f) != (size_t)sz) return 1;
    fclose(f);
    if (memcmp(buf, "K4B1", 4)) { fprintf(stderr, "NG: magic\n"); return 1; }
    g = buf + 4;
    uint32_t n_cases = rd32();

    uint8_t *stack = mmap(NULL, STACK_BYTES, PROT_READ | PROT_WRITE,
                          MAP_PRIVATE | MAP_ANON, -1, 0);
    if (stack == MAP_FAILED) { fprintf(stderr, "NG: mmap\n"); return 1; }

    size_t max_heap = 0, max_stack = 0, sum_heap = 0;
    uint32_t worst_heap_case = 0, worst_stack_case = 0;
    int max_labels = 0, max_feats = 0;
    size_t leaked = 0, unknown = 0;
    FILE *out = fopen(out_path, "wb");
    if (!out) { fprintf(stderr, "NG: %s が開けない\n", out_path); return 1; }

    for (uint32_t c = 0; c < n_cases; c++) {
        n_feat = (int)rd32();
        for (int i = 0; i < n_feat; i++) {
            rdstr(febuf[i], sizeof febuf[i]);
            feat[i] = febuf[i];
        }
        uint32_t nn = rd32();
        for (uint32_t i = 0; i < nn; i++) {          /* 期待 NJD は読み飛ばす */
            char s[1024];
            for (int k = 0; k < 11; k++) rdstr(s, sizeof s);
            rd32(); rd32(); rd32();
        }

        oj_heap_reset();
        size_t st = run_on_painted_stack(stack);
        size_t hp = oj_heap_peak();
        leaked += oj_heap_live();
        unknown += oj_heap_n_unknown();

        sum_heap += hp;
        if (hp > max_heap) { max_heap = hp; worst_heap_case = c; }
        if (st > max_stack) { max_stack = st; worst_stack_case = c; }
        if (label_count > max_labels) max_labels = label_count;
        if (n_feat > max_feats) max_feats = n_feat;

        fprintf(out, "# case %u labels=%d\n%s", c, label_count, label_join);
    }
    fclose(out);

    printf("\n=== G22: 1 文あたりのピーク（n=%u、ホスト実測）===\n", n_cases);
    printf("  ヒープ  最大 %8zu B（case %u）/ 平均 %8zu B\n",
           max_heap, worst_heap_case, sum_heap / n_cases);
    printf("  スタック 最大 %8zu B（case %u）\n", max_stack, worst_stack_case);
    printf("  合計    最大 %8zu B\n", max_heap + max_stack);
    printf("  ラベル最大 %d 本 / 形態素最大 %d 個 / MAXBUFLEN = %d\n",
           max_labels, max_feats, OJ_MAXBUFLEN);
    printf("  ラベルバッファの占める分 = %d × %d = %d B（ヒープ最大の %.1f%%）\n",
           max_labels, OJ_MAXBUFLEN, max_labels * OJ_MAXBUFLEN,
           100.0 * max_labels * OJ_MAXBUFLEN / (double)max_heap);

    int bad = 0;
    printf("\n=== 健全性 ===\n");
    printf("  %s 解放し損ね %zu B\n", leaked ? "NG " : "OK ", leaked);
    printf("  %s 追跡外の free %zu 回\n", unknown ? "NG " : "OK ", unknown);
    if (leaked || unknown) bad = 1;
    if (max_stack == 0 || max_stack >= STACK_BYTES - 4096) {
        printf("  NG  スタック計測が怪しい（%zu / %u）\n", max_stack, STACK_BYTES);
        bad = 1;
    }
    printf("\n  ラベルを %s に出した。詰めた版と `diff` すれば G24。\n", out_path);
    printf("\n%s\n", bad ? "NG!" : "OK  計測できた");
    free(buf);
    return bad;
}
