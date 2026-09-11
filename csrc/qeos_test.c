/* G33: **疑問 EOS の 4 種と、U+301C の正規化**が効いているか。
 *
 *   make -C csrc qeos
 *
 * ⚠️ **辞書もコーパスも pyopenjtalk も要らない。** `label_ids_convert()` は
 *    ラベル列とテキストだけを見るので、**合成した最小ラベル 3 本**で足りる。
 *    → `all-test` と CI で回る。
 *
 * ## なぜ要るのか
 *
 * `？` + `〜`(U+301C WAVE DASH) で終わる文が、**端末では疑問にならなかった**
 * （[M-103](../docs/measurements.md#m-103) §2）。ホストは `kana_g2p.normalize_input()` で
 * U+FF5E に寄せてから G2P に渡すのに、端末は生のテキストを見ていた。
 * ⚠️ **日本語で普通に使われるのは U+301C の方**で、コーパスの `?~` 学習行 10 本は
 *    **全部 U+301C 綴り**（U+FF5E は両コーパスに 0 件）。
 *
 * ⚠️ **既存のゲートは原理的に盲だった。** `make -C csrc label-ids` の既定は n=298 で、
 *    該当する held-out 2 文をサンプルしない。**直しても既定は緑のまま**なので、
 *    この専用ゲートが無いと回帰を防げない。
 *
 * ## 陽性対照
 *
 * `-DQEOS_TEST_NO_NORMALIZE=1` でビルドすると `label_ids.c` の正規化を外す。
 * **U+301C のケースが落ちなければ、このゲートは空虚である。**
 */
#include <stdio.h>
#include <stdint.h>
#include <string.h>

#include "label_ids.h"

/* 合成した最小ラベル。`label_phoneme` は `-` と `+` の間を取り、
 * `label_prosody` は `/A:` を読む。⚠️ **先頭の sil は捨てられ、末尾の sil が
 * 疑問記号に化ける**（label_ids.c）。 */
static const char *const LABELS[] = {
    "xx^xx-sil+a=xx/A:xx+xx+xx/F:xx_xx",
    "xx^sil-a+sil=xx/A:0+1+1/F:1_0",
    "sil^a-sil+xx=xx/A:xx+xx+xx/F:xx_xx",
};
#define N_LABELS ((int)(sizeof LABELS / sizeof LABELS[0]))

/* `^`=1 `$`=2 `?`=3 `?!`=4 `?.`=5 `?~`=6 `_`=0（token_table.h） */
#define ID_CARET 1
#define ID_DOLLAR 2

static int fail = 0;

static void expect(const char *label, const char *text, int32_t want_eos) {
    int32_t ids[64], n = 0;
    label_ids_status st = label_ids_convert(LABELS, N_LABELS, text, ids, 64, &n);
    if (st != LABEL_IDS_OK) {
        printf("  NG! %-34s st=%d（%s）\n", label, (int)st, label_ids_strerror(st));
        fail = 1;
        return;
    }
    /* 末尾は ... <EOS 記号> 0 2 か、記号が無ければ ... 0 2 */
    int32_t got_eos = -1;
    if (n >= 3 && ids[n-1] == ID_DOLLAR && ids[n-2] == 0) got_eos = ids[n-3];
    if (n >= 1 && ids[0] != ID_CARET) {
        printf("  NG! %-34s 先頭が ^ でない（%d）\n", label, (int)ids[0]);
        fail = 1;
        return;
    }
    if (want_eos < 0) {
        /* 疑問でない: 末尾は音素 → 0 → $ なので、got_eos は記号ではない */
        if (got_eos >= ID_CARET && got_eos <= 6 && got_eos != ID_CARET) {
            printf("  NG! %-34s 疑問でないのに記号 %d が付いた\n", label, (int)got_eos);
            fail = 1;
            return;
        }
        printf("  OK  %-34s 疑問記号なし（n=%d）\n", label, (int)n);
        return;
    }
    if (got_eos != want_eos) {
        printf("  NG! %-34s EOS=%d（期待 %d）  n=%d\n",
               label, (int)got_eos, (int)want_eos, (int)n);
        fail = 1;
        return;
    }
    printf("  OK  %-34s EOS=%d（n=%d）\n", label, (int)got_eos, (int)n);
}

int main(void) {
#ifdef QEOS_TEST_NO_NORMALIZE
    printf("=== G33 疑問 EOS（⚠️ **陽性対照ビルド: 正規化なし**）===\n");
#else
    printf("=== G33 疑問 EOS と U+301C の正規化 ===\n");
#endif

    /* --- 4 種の疑問符（ASCII と全角） --- */
    expect("`?`",              "a?",                              3);
    expect("`？`(U+FF1F)",     "a\xEF\xBC\x9F",                   3);
    expect("`?!`",             "a?!",                             4);
    expect("`？！`",           "a\xEF\xBC\x9F\xEF\xBC\x81",       4);
    expect("`?.`",             "a?.",                             5);
    expect("`？。`",           "a\xEF\xBC\x9F\xE3\x80\x82",       5);
    expect("`?~`",             "a?~",                             6);
    expect("`？～`(U+FF5E)",   "a\xEF\xBC\x9F\xEF\xBD\x9E",       6);

    /* --- ⚠️ 本題: U+301C（〜 WAVE DASH）--- */
    printf("\n  ⚠️ U+301C（日本語で普通に使われる方。M-103 §2）:\n");
#ifdef QEOS_TEST_NO_NORMALIZE
    /* 陽性対照: 正規化が無いと疑問にならない = ここで落ちるのが正しい */
    expect("`？〜`(U+301C)",   "a\xEF\xBC\x9F\xE3\x80\x9C",       6);
    expect("`〜？`(U+301C)",   "a\xE3\x80\x9C\xEF\xBC\x9F",       6);
#else
    expect("`？〜`(U+301C)",   "a\xEF\xBC\x9F\xE3\x80\x9C",       6);
    expect("`〜？`(U+301C)",   "a\xE3\x80\x9C\xEF\xBC\x9F",       6);
#endif

    /* --- 陰性対照: 疑問でないものに記号を付けない --- */
    printf("\n  陰性対照:\n");
    expect("疑問でない（`a`）",       "a",                        -1);
    expect("`。` だけ",               "a\xE3\x80\x82",            -1);
    expect("`〜` だけ（? が無い）",   "a\xE3\x80\x9C",            -1);
    expect("`～` だけ（? が無い）",   "a\xEF\xBD\x9E",            -1);

    /* --- 末尾の空白を無視するか --- */
    printf("\n  末尾の空白:\n");
    expect("`？〜` + 空白と改行",
           "a\xEF\xBC\x9F\xE3\x80\x9C  \n\r\t",                   6);

#ifdef QEOS_TEST_NO_NORMALIZE
    if (fail) {
        printf("\nOK  陽性対照: 正規化を外すと U+301C のケースが落ちた\n");
        return 0;
    }
    printf("\nNG! 陽性対照が落ちなかった = **このゲートは空虚**\n");
    return 1;
#else
    if (fail) {
        printf("\nNG! 疑問 EOS が期待と違う\n");
        return 1;
    }
    printf("\nOK  4 種の疑問符 + U+301C の正規化 + 陰性対照 4 件\n");
    printf("⚠️ 見ていないもの: **辞書経路の形態素分割**（〜 は音素を生まないので "
           "ids には出ない。M-103 / 実測）/ ホストの `U+2212` `U+00A0` の正規化"
           "（**端末には入れていない** — U+FF0D は辞書に無い）\n");
    return 0;
#endif
}
