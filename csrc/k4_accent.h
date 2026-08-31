/* K-4: pyopenjtalk-plus のアクセント規則 4 段を C に移植したもの。
 *
 * 根拠は docs/research/k1-kanji-katakana-ondevice.md §5。
 * **この 4 段が C 実装のアクセント天井 76% の正体**で、外部資源は
 * `_DAN_MAP`（かな 76 件）しか要らない。SudachiDict 217 MB と nani ONNX の
 * 寄与は合計 0.13pt しかない。
 *
 * 規約: malloc を呼ばない。ノード列はその場で書き換える。
 */
#ifndef K4_ACCENT_H
#define K4_ACCENT_H

#include <stddef.h>

#define K4_STR_MAX   64
#define K4_PRON_MAX 128

/* NJD ノードのうち、4 段が読む/書くフィールドだけ。 */
typedef struct {
    char pos[K4_STR_MAX];
    char ctype[K4_STR_MAX];
    char cform[K4_STR_MAX];
    char orig[K4_STR_MAX];
    char pron[K4_PRON_MAX];      /* suppress_u が書き換える */
    char read[K4_PRON_MAX];
    int  acc;                    /* filler / retreat / chaining が書き換える */
    int  mora_size;
    int  chain_flag;             /* filler が書き換える */
} k4_node_t;

/* かな → 母音（'a','i','u','e','o'）。_DAN_MAP に対応。 */
typedef struct {
    const char *kana;            /* UTF-8 1 文字 */
    char        dan;
} k4_dan_t;

enum {
    K4_FILLER      = 1u << 0,    /* modify_filler_accent */
    K4_SUPPRESS_U  = 1u << 1,    /* suppress_unnatural_auxiliary_u_long_vowel */
    K4_RETREAT     = 1u << 2,    /* retreat_acc_nuc */
    K4_CHAINING    = 1u << 3,    /* modify_acc_after_chaining */
    K4_ALL         = 0xF
};

/* stage_mask のビットが立っている段だけを、**この順で**適用する。 */
void k4_apply(k4_node_t *nodes, int n, unsigned stage_mask,
              const k4_dan_t *dan, int n_dan);

#endif
