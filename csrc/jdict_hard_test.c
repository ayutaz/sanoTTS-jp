/* M-100: jdict_open の入力検査（辞書ファイルが要らないので all-test と CI に入る）。
 *
 * ⚠️ **このゲートは「壊れた blob を拒むか」だけを見る。** 解析の正しさは
 *    `make -C csrc jdict`（辞書と pyopenjtalk が要るので all-test の外）が見る。
 *
 * ⚠️ **陽性対照は別バイナリ。** `-DJDICT_TEST_WEAK=1` で検査を外したコアを
 *    別に建て、**同じ壊れた blob を「通してしまう」こと**を示す。
 *    これが無いと「全部拒んだ」は「そもそも blob が作れていない」と区別できない
 *    （csrc/erf_test.c / csrc/range_test.c と同じ作法）。
 *
 * 塞いだ欠陥（どれも実測。M-100）:
 *   1. matrix セクションの長さを一切検査していなかった
 *      → 別形式のデータを int16 として読み進め、10/10 文が 1 文字ずつに刻まれた
 *   2. matrix が無くても jdict_open が成功していた
 *      → jdict_trans が全遷移 0 を返す = 全コスト 0 の Viterbi。警告ゼロ
 *   3. セクション表を丸ごと検査していなかった（引いたセクションしか見ない）
 *   4. `n` は端末ではパーティション長（実 blob より 125,776 B 大きい）
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "jdict.h"

static int fails;
static void ok_(const char *name, int cond, const char *detail) {
    printf("  %s %s%s%s\n", cond ? "OK " : "NG ", name,
           detail && detail[0] ? "    " : "", detail ? detail : "");
    if (!cond) fails++;
}

/* ---------------------------------------------------------------- blob を組む */

#define MAX_SEC 24

typedef struct {
    char           name[9];
    const uint8_t *data;
    uint32_t       len;
} sec_in;

/* セクションを 16 B 境界に並べた blob を作る（src/saanotts_jp/jdict.py の to_bytes と同じ配置）。
 * ⚠️ **宣言長を実長からずらせるように len_fudge を持つ。** これが対照の本体。 */
static uint8_t *build_blob(uint16_t version, const sec_in *secs, int n_sec,
                           int fudge_idx, long fudge, size_t *out_n) {
    size_t head = 8u + 16u * (size_t)n_sec;
    size_t off = (head + 15u) & ~(size_t)15u;
    size_t offs[MAX_SEC];
    for (int i = 0; i < n_sec; i++) {
        offs[i] = off;
        off += secs[i].len;
        off = (off + 15u) & ~(size_t)15u;
    }
    size_t total = off;
    /* ⚠️ **末尾に余白を取らない。** かつて `total + 64` にしていたため、
     *    `jdict_open` が宣言長を見る前にヘッダを読む越境を ASan でも検出できなかった
     *    （レビューで指摘。M-100 §8）。 */
    uint8_t *b = (uint8_t *)calloc(1, total);
    if (!b) return NULL;
    memcpy(b, "K1D1", 4);
    b[4] = (uint8_t)(version & 0xFF); b[5] = (uint8_t)(version >> 8);
    b[6] = (uint8_t)(n_sec & 0xFF);   b[7] = (uint8_t)(n_sec >> 8);
    for (int i = 0; i < n_sec; i++) {
        uint8_t *e = b + 8 + 16u * i;
        memset(e, 0, 8);
        memcpy(e, secs[i].name, strlen(secs[i].name));
        uint32_t o = (uint32_t)offs[i];
        long l = (long)secs[i].len + (i == fudge_idx ? fudge : 0);
        if (l < 0) l = 0;
        uint32_t ll = (uint32_t)l;
        e[8]  = (uint8_t)(o);       e[9]  = (uint8_t)(o >> 8);
        e[10] = (uint8_t)(o >> 16); e[11] = (uint8_t)(o >> 24);
        e[12] = (uint8_t)(ll);      e[13] = (uint8_t)(ll >> 8);
        e[14] = (uint8_t)(ll >> 16); e[15] = (uint8_t)(ll >> 24);
        if (secs[i].len) memcpy(b + offs[i], secs[i].data, secs[i].len);
    }
    *out_n = total;
    return b;
}

/* jdict_open が必須にしている 8 セクションの、**中身は空でよい**最小セット。
 * ⚠️ ここで作るのは「開けるか」を見るための骨だけ。解析はしない。 */
#define LS 3u
#define RS 4u

static uint8_t g_louds[64];
static uint8_t g_counts[4];        /* 見出し語 4 件・各 1 エントリ */
static uint8_t g_surfck[4];
static uint8_t g_records[4 * 9];   /* 4 entries */
static uint8_t g_pool[4];
static uint8_t g_classes[16];
static uint8_t g_keytab[4];
static uint8_t g_keyesc[4];
static uint8_t g_char[4 + 32 + 4 * 8];      /* n_char_cats=1 + 名前 32 B + info 8 件 */
static uint8_t g_unk[4 + 8];
static uint8_t g_matrix[4 + 2 * LS * RS];
/* matrixa（行ごとアフィン uint8。M-104）: 8 + 4*RS + LS*RS */
static uint8_t g_matrixa[8 + 4 * RS + LS * RS];

static void init_payloads(void) {
    memset(g_louds, 0, sizeof g_louds);          /* bitlen/n_nodes/... は 0 でよい */
    /* ⚠️ **層をまたぐ相互検証を通るように辻褄を合わせる**（M-100 §8 の 3）:
     *    n_surfaces = len(counts) = 4 / sum(counts) = 4 = n_entries = len(records)/9 /
     *    len(surfck) = 4*ceil(4/32) = 4 */
    memset(g_counts, 1, sizeof g_counts);
    memset(g_surfck, 0, sizeof g_surfck);
    memset(g_records, 0, sizeof g_records);
    memset(g_pool, 0, sizeof g_pool);
    memset(g_classes, 0, sizeof g_classes);      /* n_classes = 0 */
    memset(g_keytab, 0, sizeof g_keytab);
    memset(g_keyesc, 0, sizeof g_keyesc);
    memset(g_char, 0, sizeof g_char); g_char[0] = 1;   /* n_char_cats = 1 */
    memset(g_unk, 0, sizeof g_unk);
    g_matrix[0] = (uint8_t)LS; g_matrix[1] = 0;
    g_matrix[2] = (uint8_t)RS; g_matrix[3] = 0;
    for (unsigned i = 0; i < LS * RS; i++) {
        int16_t v = (int16_t)(100 * (int)(i / LS) + (int)(i % LS));
        g_matrix[4 + 2 * i]     = (uint8_t)((uint16_t)v & 0xFF);
        g_matrix[4 + 2 * i + 1] = (uint8_t)((uint16_t)v >> 8);
    }
    /* matrixa: lo = 0 / span = 100 / q は 0..255 を巡回。
     * ⚠️ **値の正しさはここでは見ない**（それは make -C csrc matrixa の仕事）。
     *    ここで見るのは**壊れた blob を拒むか**だけ。 */
    g_matrixa[0] = (uint8_t)LS; g_matrixa[2] = (uint8_t)RS;
    g_matrixa[4] = 8;                                  /* bits */
    for (unsigned r = 0; r < RS; r++) {
        g_matrixa[8 + 2 * r] = 0; g_matrixa[8 + 2 * r + 1] = 0;         /* lo  = 0 */
        g_matrixa[8 + 2 * RS + 2 * r] = 100;                            /* span = 100 */
    }
    for (unsigned i = 0; i < LS * RS; i++) {
        g_matrixa[8 + 4 * RS + i] = (uint8_t)(i * 37u);
    }
}

static int base_secs(sec_in *out) {
    int n = 0;
    out[n++] = (sec_in){"louds",   g_louds,   (uint32_t)sizeof g_louds};
    out[n++] = (sec_in){"counts",  g_counts,  (uint32_t)sizeof g_counts};
    out[n++] = (sec_in){"surfck",  g_surfck,  (uint32_t)sizeof g_surfck};
    out[n++] = (sec_in){"records", g_records, (uint32_t)sizeof g_records};
    out[n++] = (sec_in){"pool",    g_pool,    (uint32_t)sizeof g_pool};
    out[n++] = (sec_in){"classes", g_classes, (uint32_t)sizeof g_classes};
    out[n++] = (sec_in){"keytab",  g_keytab,  (uint32_t)sizeof g_keytab};
    out[n++] = (sec_in){"keyesc",  g_keyesc,  (uint32_t)sizeof g_keyesc};
    out[n++] = (sec_in){"char",    g_char,    (uint32_t)sizeof g_char};
    out[n++] = (sec_in){"unk",     g_unk,     (uint32_t)sizeof g_unk};
    /* ⚠️ **matrix は最後に置く。** 「宣言長 0 のセクションが blob の末尾にある」
     *    ケース（rd16 が越境しないか）を張るため。 */
    out[n++] = (sec_in){"matrix",  g_matrix,  (uint32_t)sizeof g_matrix};
    return n;
}
#define LOUDS_IDX   0
#define CLASSES_IDX 5
#define CHAR_IDX    8
#define UNK_IDX     9
#define MATRIX_IDX 10

/* ---------------------------------------------------------------- ケース */

/* ⚠️ **期待値は「開けたか」ではなく `jdict_open` の返り値そのもの。**
 *    「開けなかった」でまとめると、**別の検査が偶然拾った**のを
 *    「名前どおりの検査が効いた」と読んでしまう（レビューで指摘）。 */
typedef struct { const char *name; int expect; } caze;

/* 壊し方を 1 つ適用して jdict_open の戻り値を返す。 */
static int open_case(int which, size_t extra_n, jdict_t *d_out, size_t *blob_len_out) {
    sec_in secs[MAX_SEC];
    int n_sec = base_secs(secs);
    int fudge_idx = -1; long fudge = 0;
    uint16_t ver = 2;

    switch (which) {
    case 0: break;                                   /* 正常 */
    case 1: strcpy(secs[MATRIX_IDX].name, "matri8"); break;   /* 名前を 1 B 変える */
    case 2: fudge_idx = MATRIX_IDX; fudge = +1; break;        /* 宣言長 +1 */
    case 3: fudge_idx = MATRIX_IDX; fudge = -1; break;        /* 宣言長 -1 */
    case 4: n_sec = MATRIX_IDX; break;                        /* matrix ごと消す */
    case 5: ver = 7; break;                                   /* 未知の版 */
    case 8: fudge_idx = MATRIX_IDX; fudge = -(long)sizeof g_matrix; break;  /* 宣言長 0（末尾） */
    case 9: fudge_idx = LOUDS_IDX;  fudge = 4 - (long)sizeof g_louds; break; /* 宣言長 4 */
    case 12: fudge_idx = CHAR_IDX;  fudge = -(long)sizeof g_char; break;    /* 宣言長 0 */
    case 13: fudge_idx = UNK_IDX;   fudge = -(long)sizeof g_unk; break;     /* 宣言長 0 */
    /* ⚠️ **ペイロードごと消す。** ケース 8 は宣言長だけ 0 にしていて実データが
     *    残るので、**越境を再現しない**（ASan で 0 件だった）。ここは matrix の
     *    実体を消して offset を blob 末尾ちょうどに置き、`rd16` が 1 B でも
     *    はみ出せば ASan が鳴る形にする。 */
    case 14: secs[MATRIX_IDX].len = 0; break;
    /* M-100 §8 の 3: 層をまたぐ相互検証（宣言長をそのまま使うセクション） */
    case 15: fudge_idx = 3; fudge = +9; break;    /* records を 1 件ぶん増やす（counts と合わない） */
    /* --- matrixa（行ごとアフィン uint8。M-104）--- */
    /* ⚠️ **17 は「開ける」ことを見る唯一のケース。** これが無いと、
     *    18〜21 が全部通っても「matrixa は常に拒まれる」だけかもしれない。 */
    case 17:                                                  /* 正しい matrixa 単独 */
        secs[MATRIX_IDX] = (sec_in){"matrixa", g_matrixa, (uint32_t)sizeof g_matrixa};
        strcpy(secs[MATRIX_IDX].name, "matrixa");
        break;
    case 18:                                                  /* matrix と matrixa の両方 */
        secs[n_sec++] = (sec_in){"matrixa", g_matrixa, (uint32_t)sizeof g_matrixa};
        strcpy(secs[n_sec - 1].name, "matrixa");
        break;
    case 19:                                                  /* bits = 4（未知の量子化幅） */
        secs[MATRIX_IDX] = (sec_in){"matrixa", g_matrixa, (uint32_t)sizeof g_matrixa};
        strcpy(secs[MATRIX_IDX].name, "matrixa");
        g_matrixa[4] = 4;
        break;
    case 20:                                                  /* 宣言長 -1 */
        secs[MATRIX_IDX] = (sec_in){"matrixa", g_matrixa, (uint32_t)sizeof g_matrixa};
        strcpy(secs[MATRIX_IDX].name, "matrixa");
        fudge_idx = MATRIX_IDX; fudge = -1;
        break;
    case 21:                                                  /* 実体ごと消す（ASan で見る） */
        secs[MATRIX_IDX] = (sec_in){"matrixa", g_matrixa, 0u};
        strcpy(secs[MATRIX_IDX].name, "matrixa");
        break;
    case 16: fudge_idx = 2; fudge = +4; break;    /* surfck を 4 B 増やす（見出し語数から計算した値と合わない） */
    default: break;
    }

    size_t n = 0;
    uint8_t *b = build_blob(ver, secs, n_sec, fudge_idx, fudge, &n);
    if (!b) return -99;

    if (which == 6) {                                /* lsize = 0 */
        uint32_t off = (uint32_t)b[8 + 16 * MATRIX_IDX + 8]
                     | ((uint32_t)b[8 + 16 * MATRIX_IDX + 9] << 8)
                     | ((uint32_t)b[8 + 16 * MATRIX_IDX + 10] << 16)
                     | ((uint32_t)b[8 + 16 * MATRIX_IDX + 11] << 24);
        b[off] = 0; b[off + 1] = 0;
    }
    if (which == 7) {                                /* セクション表が blob の外を指す */
        uint8_t *e = b + 8 + 16 * MATRIX_IDX;
        uint32_t huge = (uint32_t)(n + 1024);
        e[8] = (uint8_t)huge; e[9] = (uint8_t)(huge >> 8);
        e[10] = (uint8_t)(huge >> 16); e[11] = (uint8_t)(huge >> 24);
    }

    /* --- 本体を書き換える壊し方（build_blob の後） --- */
    {
        uint32_t off;
        #define SEC_OFF(idx) ((uint32_t)b[8 + 16 * (idx) + 8] \
                            | ((uint32_t)b[8 + 16 * (idx) + 9] << 8) \
                            | ((uint32_t)b[8 + 16 * (idx) + 10] << 16) \
                            | ((uint32_t)b[8 + 16 * (idx) + 11] << 24))
        if (which == 10) {          /* louds の導出長がセクションを超える */
            off = SEC_OFF(LOUDS_IDX);
            b[off] = 0xFF; b[off+1] = 0xFF; b[off+2] = 0xFF; b[off+3] = 0x0F;
        }
        if (which == 11) {          /* classes の n_classes を巨大に（underflow を誘う） */
            off = SEC_OFF(CLASSES_IDX);
            b[off] = 0xFF; b[off+1] = 0xFF; b[off+2] = 0xFF; b[off+3] = 0x0F;
        }
        #undef SEC_OFF
    }

    jdict_t d;
    int r = jdict_open(&d, b, n + extra_n);
    if (d_out) *d_out = d;
    if (blob_len_out) *blob_len_out = (r == 0) ? d.blob_len : 0;
    /* ⚠️ **blob は解放しない。** d が中を指したままなので、呼び出し側が
     *    使い終わるまで生かす必要がある。ゲートは 1 発ずつなので漏らして構わない
     *    （テストバイナリの寿命 = プロセスの寿命）。 */
    (void)b;
    return r;
}

static const caze CASES[] = {
    /*  0 */ {"正常な blob",                                      0},
    /*  1 */ {"matrix の名前を 1 B 変える",                       JDICT_ERR_MATRIX},
    /*  2 */ {"matrix の宣言長 +1",                               JDICT_ERR_MATRIX},
    /*  3 */ {"matrix の宣言長 -1",                               JDICT_ERR_MATRIX},
    /*  4 */ {"matrix セクションごと削除",                         JDICT_ERR_MATRIX},
    /*  5 */ {"未知の版 (ver=7)",                                 JDICT_ERR_VERSION},
    /*  6 */ {"matrix の lsize = 0",                              JDICT_ERR_MATRIX},
    /*  7 */ {"セクションが blob の外を指す",                      JDICT_ERR_SECTAB},
    /*  8 */ {"matrix の宣言長 0（blob 末尾。rd16 が越境しないか）", JDICT_ERR_MATRIX},
    /*  9 */ {"louds の宣言長 4（ヘッダ 20 B に足りない）",          -3},
    /* 10 */ {"louds の導出長がセクションを超える",                 -3},
    /* 11 */ {"classes の n_classes が巨大（pos6tab_len が underflow）", -8},
    /* 12 */ {"char の宣言長 0（n_codepoints が underflow）",       JDICT_ERR_CHAR},
    /* 13 */ {"unk の宣言長 0（unk_len が 0xFFFFFFFC）",           JDICT_ERR_UNK},
    /* 14 */ {"matrix の実体ごと消す（offset が blob 末尾。ASan で見る）", JDICT_ERR_MATRIX},
    /* 15 */ {"records が counts の合計と合わない",                 -6},
    /* 16 */ {"surfck の長さが見出し語数から計算した値と合わない",   -5},
    /* 17 */ {"**正しい matrixa 単独**（開けること）",              0},
    /* 18 */ {"matrix と matrixa の両方がある",                     JDICT_ERR_MATRIX},
    /* 19 */ {"matrixa の bits = 4（未知の量子化幅）",              JDICT_ERR_MATRIX},
    /* 20 */ {"matrixa の宣言長 -1",                               JDICT_ERR_MATRIX},
    /* 21 */ {"matrixa の実体ごと消す（ASan で見る）",              JDICT_ERR_MATRIX},
};
#define N_CASES ((int)(sizeof CASES / sizeof CASES[0]))

int main(void) {
    init_payloads();

#if defined(JDICT_TEST_WEAK) && JDICT_TEST_WEAK
    /* ---- 陽性対照の側 ---- 検査を外したコアが「通してしまう」ことを示す */
    printf("=== 陽性対照: JDICT_TEST_WEAK=1（検査を外したコア）===\n");
    /* ⚠️ **ケースごとに要求する。** 「まとめて N 件以上」だと、
     *    **死んでいる検査を 1 本見逃す**（レビューで指摘。ある壊し方が
     *    名前どおりの検査ではなく別の検査に拾われていても気づけない）。
     * ⚠️ 版検査（ケース 5）だけは JD_CHECK の外にあるので weak でも落ちる。 */
    static const int WEAK_MUST_LEAK[] = {1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16,
                                        18, 19, 20, 21};
    int n_must = (int)(sizeof WEAK_MUST_LEAK / sizeof WEAK_MUST_LEAK[0]);
    int leaked = 0;
    for (int k = 0; k < n_must; k++) {
        int i = WEAK_MUST_LEAK[k];
        int r = open_case(i, 0, NULL, NULL);
        int opened = (r == 0);
        printf("  %-44s jdict_open = %-4d %s\n", CASES[i].name, r,
               opened ? "← 通してしまった" : "← ⚠️ **落ちた**（この検査は JD_CHECK の外）");
        if (opened) leaked++;
    }
    printf("\n  通してしまった壊れ方: %d / %d\n", leaked, n_must);
    ok_("陽性対照: JD_CHECK を外すと全部通る", leaked == n_must, "");
    {
        int r5 = open_case(5, 0, NULL, NULL);
        char det[64]; snprintf(det, sizeof det, "jdict_open = %d", r5);
        ok_("版検査だけは JD_CHECK の外なので weak でも落ちる", r5 == JDICT_ERR_VERSION, det);
    }
    printf("\n%s\n", fails ? "NG!" : "OK  陽性対照: 検査を外したコアは壊れた blob を通す");
    return fails ? 1 : 0;
#else
    printf("=== G-H1: jdict_open の入力検査（M-100）===\n");
    for (int i = 0; i < N_CASES; i++) {
        int r = open_case(i, 0, NULL, NULL);
        char det[96];
        snprintf(det, sizeof det, "jdict_open = %d（期待 %d）", r, CASES[i].expect);
        ok_(CASES[i].name, r == CASES[i].expect, det);
    }

    printf("\n=== G-H2: `n` が実 blob より大きくても blob_len は実 extent（M-100 §3）===\n");
    {
        size_t l0 = 0, l1 = 0;
        int r0 = open_case(0, 0, NULL, &l0);
        int r1 = open_case(0, 125776, NULL, &l1);   /* 端末のパーティション余り */
        char det[128];
        snprintf(det, sizeof det, "n +0 -> %zu B / n +125,776 -> %zu B", l0, l1);
        ok_("余分な n を渡しても blob_len が同じ", r0 == 0 && r1 == 0 && l0 == l1 && l0 > 0, det);
    }

    printf("\n=== G-H3: 開けた blob では行列が読める（検査が値を壊していない）===\n");
    {
        jdict_t d; size_t bl = 0;
        int r = open_case(0, 0, &d, &bl);
        int good = (r == 0) && (d.lsize == LS) && (d.rsize == RS);
        int vals_ok = good;
        for (unsigned lc = 0; good && lc < RS; lc++)
            for (unsigned rc = 0; rc < LS; rc++)
                if (jdict_trans(&d, (uint16_t)rc, (uint16_t)lc) != (int16_t)(100 * lc + rc))
                    vals_ok = 0;
        char det[96];
        snprintf(det, sizeof det, "lsize=%u rsize=%u", (unsigned)d.lsize, (unsigned)d.rsize);
        ok_("trans(rc,lc) == 100*lc + rc（全 12 セル）", vals_ok, det);
    }

    printf("\n%s\n", fails ? "NG!" : "OK  G-H1 / G-H2 / G-H3 通過");
    return fails ? 1 : 0;
#endif
}
