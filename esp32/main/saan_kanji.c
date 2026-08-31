#include "saan_kanji.h"

#include <stdio.h>
#include <string.h>

#include "k4_accent.h"
#include "k4b_njd.h"
#include "k7_label2ids.h"
#include "k7_dan.h"
#include "openjtalk/njd.h"
#include "openjtalk/jpcommon.h"
#include "openjtalk/mecab2njd.h"
#include "openjtalk/njd2jpcommon.h"
#include "openjtalk/njd_set_pronunciation.h"
#include "openjtalk/njd_set_digit.h"
#include "openjtalk/njd_set_accent_phrase.h"
#include "openjtalk/njd_set_accent_type.h"
#include "openjtalk/njd_set_unvoiced_vowel.h"
#include "openjtalk/njd_set_long_vowel.h"

/* ⚠️ **.bss には置けない。** 静的に持つと DRAM が 419 KB 溢れ、
 *    heap から取ろうとしても**空きが 20,964 B しか無い**（どちらも QEMU で実測）。
 *    ⚠️ PSRAM は使えない — **QEMU が octal PSRAM を持っていない**。
 *
 * **大きい 2 つ（feat / k4 ノード）は合成用 arena から切り出す。**
 * G2P と合成は同時に走らないので競合しない。小さい配列（ポインタ表など）だけ
 * .bss に残す（合計 4.6 KB）。
 *
 * ⚠️ **Viterbi の作業領域は 32 KB あれば held-out 298 文すべてで足りる**
 *    （16 KB だと 1 文落ちる。ホストで実測）。余裕を見て 48 KB 取る。
 *
 * ⚠️ **トークン数の上限は 96。** 端末は ids 350 個までしか喋らない
 *    （`SAAN_MAX_IDS`）ので、1 文あたりの形態素はそれよりずっと少ない
 *    （K-5 の最長文 98 文字で 67 個）。 */
#define KJ_MAX_TOK    96
#define KJ_MAX_LABEL  512
#define KJ_KEY_MAX    1024
#define KJ_FEAT_MAX   320
#define KJ_VITERBI_N  (48u * 1024u)

/* .bss（小さいものだけ） */
static char s_key[KJ_KEY_MAX];
static k1_token_t s_tok[KJ_MAX_TOK];
static char *s_feat[KJ_MAX_TOK];
static const char *s_lab[KJ_MAX_LABEL];

/* arena から切り出す（大きいもの） */
static char *s_feat_flat;
static k4_node_t *s_k4;

size_t saan_kanji_workbytes(void) {
    return (size_t)KJ_MAX_TOK * KJ_FEAT_MAX
         + sizeof(k4_node_t) * KJ_MAX_TOK + KJ_VITERBI_N;
}

int saan_kanji_init(void) { return 1; }   /* 確保はしない。arena を借りる */

/* arena の先頭に大きい配列を並べ、残りを Viterbi に回す。
 * ⚠️ **毎回やり直す。** 合成が arena を上書きするので、ポインタを
 *    起動時に 1 回だけ作ると次の発話で壊れたところを指す。 */
static void *layout(void *arena, size_t arena_n, size_t *vit_n) {
    unsigned char *p = (unsigned char *)arena;
    size_t need = saan_kanji_workbytes();
    if (arena_n < need) { *vit_n = 0; return NULL; }
    s_feat_flat = (char *)p;                 p += (size_t)KJ_MAX_TOK * KJ_FEAT_MAX;
    /* k4_node_t は 4 バイト境界でよいが、16 に揃えておく */
    p = (unsigned char *)(((uintptr_t)p + 15u) & ~(uintptr_t)15u);
    s_k4 = (k4_node_t *)(void *)p;           p += sizeof(k4_node_t) * KJ_MAX_TOK;
    p = (unsigned char *)(((uintptr_t)p + 15u) & ~(uintptr_t)15u);
    for (int i = 0; i < KJ_MAX_TOK; i++) s_feat[i] = s_feat_flat + (size_t)i * KJ_FEAT_MAX;
    *vit_n = arena_n - (size_t)(p - (unsigned char *)arena);
    return p;
}

const char *saan_kanji_strerror(saan_kanji_status s) {
    switch (s) {
    case SAAN_KANJI_OK:            return "OK";
    case SAAN_KANJI_ERR_KEY:       return "辞書の鍵に符号化できない";
    case SAAN_KANJI_ERR_ANALYZE:   return "経路が張れない";
    case SAAN_KANJI_ERR_FEATURE:   return "素性を復元できない";
    case SAAN_KANJI_ERR_IDS:       return "ids を作れない";
    case SAAN_KANJI_ERR_TOO_LONG:  return "内部バッファを超えた（短く区切ること）";
    }
    return "不明なエラー";
}

saan_kanji_status saan_kanji_to_ids(const k1_dict_t *d,
                                    const char *text, size_t nbytes,
                                    void *arena, size_t arena_n,
                                    int32_t *ids, int32_t ids_cap,
                                    int32_t *n_ids, int *n_tokens) {
    if (n_tokens) *n_tokens = 0;
    if (nbytes >= KJ_KEY_MAX) return SAAN_KANJI_ERR_TOO_LONG;
    size_t vit_n = 0;
    void *vit = layout(arena, arena_n, &vit_n);
    if (!vit || vit_n < 16u * 1024u) return SAAN_KANJI_ERR_TOO_LONG;
    size_t key_n = KJ_KEY_MAX;
    if (k1_encode_key(d, (const uint8_t *)text, nbytes,
                      (uint8_t *)s_key, &key_n) != 0)
        return SAAN_KANJI_ERR_KEY;

    int nt = k1_analyze(d, (const uint8_t *)s_key, key_n, vit, vit_n,
                        s_tok, KJ_MAX_TOK);
    if (nt <= 0) return SAAN_KANJI_ERR_ANALYZE;
    if (n_tokens) *n_tokens = nt;

    int nf = 0;
    for (int i = 0; i < nt && nf < KJ_MAX_TOK; i++) {
        char surf[128];
        if (k1_key_to_utf8(d, (const uint8_t *)s_key, s_tok[i].begin,
                           s_tok[i].end, surf, sizeof surf) < 0)
            return SAAN_KANJI_ERR_FEATURE;
        int r = -1;
        if (s_tok[i].entry & K1_UNKNOWN_FLAG) {
            /* ⚠️ **まず読みを推測する**（M-75）。落とすと語が無音で消える。 */
            r = k1_unk_guess(d, s_tok[i].entry, surf, s_feat[nf],
                             KJ_FEAT_MAX);
            if (r < 0)
                r = k1_unk_feature(d, s_tok[i].entry, surf, s_feat[nf],
                                   KJ_FEAT_MAX);
        } else {
            r = k1_entry_feature(d, s_tok[i].entry, surf, s_feat[nf],
                                 KJ_FEAT_MAX);
        }
        if (r < 0) return SAAN_KANJI_ERR_FEATURE;
        nf++;
    }
    if (nf <= 0) return SAAN_KANJI_ERR_FEATURE;

    NJD njd; JPCommon jp;
    NJD_initialize(&njd);
    JPCommon_initialize(&jp);
    mecab2njd(&njd, s_feat, nf);
    njd_set_pronunciation(&njd);
    k4b_before_chaining(&njd);
    njd_set_digit(&njd);
    njd_set_accent_phrase(&njd);
    njd_set_accent_type(&njd);
    njd_set_unvoiced_vowel(&njd);
    njd_set_long_vowel(&njd);

    int nk = 0;
    for (NJDNode *p = njd.head; p && nk < KJ_MAX_TOK; p = p->next, nk++) {
        snprintf(s_k4[nk].pos,   K4_STR_MAX,  "%s", NJDNode_get_pos(p));
        snprintf(s_k4[nk].ctype, K4_STR_MAX,  "%s", NJDNode_get_ctype(p));
        snprintf(s_k4[nk].cform, K4_STR_MAX,  "%s", NJDNode_get_cform(p));
        snprintf(s_k4[nk].orig,  K4_STR_MAX,  "%s", NJDNode_get_orig(p));
        snprintf(s_k4[nk].pron,  K4_PRON_MAX, "%s", NJDNode_get_pron(p));
        snprintf(s_k4[nk].read,  K4_PRON_MAX, "%s", NJDNode_get_read(p));
        s_k4[nk].acc = NJDNode_get_acc(p);
        s_k4[nk].mora_size = NJDNode_get_mora_size(p);
        s_k4[nk].chain_flag = NJDNode_get_chain_flag(p);
    }
    k4_apply(s_k4, nk, K4_ALL, k7_dan_table, K7_N_DAN);
    {
        int i = 0;
        for (NJDNode *p = njd.head; p && i < nk; p = p->next, i++) {
            NJDNode_set_pron(p, s_k4[i].pron);
            NJDNode_set_acc(p, s_k4[i].acc);
            NJDNode_set_chain_flag(p, s_k4[i].chain_flag);
        }
    }

    njd2jpcommon(&jp, &njd);
    JPCommon_make_label(&jp);
    int nl = JPCommon_get_label_size(&jp);
    char **ls = JPCommon_get_label_feature(&jp);
    if (nl > KJ_MAX_LABEL) nl = KJ_MAX_LABEL;
    for (int i = 0; i < nl; i++) s_lab[i] = ls[i];

    k7_status ks = k7_label2ids(s_lab, nl, text, ids, ids_cap, n_ids);

    JPCommon_clear(&jp);
    NJD_clear(&njd);
    return (ks == K7_OK) ? SAAN_KANJI_OK : SAAN_KANJI_ERR_IDS;
}
