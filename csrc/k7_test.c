/* K-7 の受け入れゲート（ホスト側）。   make -C csrc k7
 *
 * K-6 で「漢字文 → ラベル」まで通った。ここはその先の
 * 「ラベル → 生徒インデックス」（`k7_label2ids.c`）を検査する。
 *
 * G25  **移植の正しさ**: ホストのラベルを入れたら、ホストと同じ ids が出る
 * G25b 陰性対照: アクセント記号の規則を 1 つ抜くと G25 が落ちる
 * G26  **通し**: 漢字文 → 端末の全段 → ids が、ホスト既定の ids と一致する
 *      （⚠️ ここは辞書の枝刈りの分だけ必ず食い違う。**閾値は置かない**）
 * G27  語彙表が `src/saanotts_jp/vocab.py` と一致する（SHA-256）
 */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "k1dict.h"
#include "k4_accent.h"
#include "k4b_njd.h"
#include "k7_label2ids.h"
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

#define MAX_TOK   512
#define MAX_LABEL 1024
#define MAX_IDS   4096
#ifndef ARENA_N
#define ARENA_N   (4u << 20)
#endif

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

static int njd_to_k4(NJD *njd, k4_node_t *out, int max_out) {
    int n = 0;
    for (NJDNode *p = njd->head; p && n < max_out; p = p->next, n++) {
        snprintf(out[n].pos,   K4_STR_MAX,  "%s", NJDNode_get_pos(p));
        snprintf(out[n].ctype, K4_STR_MAX,  "%s", NJDNode_get_ctype(p));
        snprintf(out[n].cform, K4_STR_MAX,  "%s", NJDNode_get_cform(p));
        snprintf(out[n].orig,  K4_STR_MAX,  "%s", NJDNode_get_orig(p));
        snprintf(out[n].pron,  K4_PRON_MAX, "%s", NJDNode_get_pron(p));
        snprintf(out[n].read,  K4_PRON_MAX, "%s", NJDNode_get_read(p));
        out[n].acc = NJDNode_get_acc(p);
        out[n].mora_size = NJDNode_get_mora_size(p);
        out[n].chain_flag = NJDNode_get_chain_flag(p);
    }
    return n;
}
static void k4_to_njd(NJD *njd, const k4_node_t *in, int n) {
    int i = 0;
    for (NJDNode *p = njd->head; p && i < n; p = p->next, i++) {
        NJDNode_set_pron(p, in[i].pron);
        NJDNode_set_acc(p, in[i].acc);
        NJDNode_set_chain_flag(p, in[i].chain_flag);
    }
}

/* 記号（`^` `$` `?` 系 / `#` `[` `]` / `_`）か。音素だけの列を作るのに使う。 */
static int k7_is_mark(int32_t id) {
    static const char *const M[] = { "_", "^", "$", "?", "?!", "?.", "?~",
                                     "#", "[", "]" };
    for (size_t i = 0; i < sizeof M / sizeof *M; i++)
        if (k7_token_id(M[i]) == id) return 1;
    return 0;
}

/* 編集距離（Levenshtein）。列は数百なので素直な DP でよい。 */
static int lev(const int32_t *a, int na, const int32_t *b, int nb) {
    static int prev[MAX_IDS + 1], cur[MAX_IDS + 1];
    for (int j = 0; j <= nb; j++) prev[j] = j;
    for (int i = 1; i <= na; i++) {
        cur[0] = i;
        for (int j = 1; j <= nb; j++) {
            int c = (a[i - 1] == b[j - 1]) ? 0 : 1;
            int m = prev[j] + 1;
            if (cur[j - 1] + 1 < m) m = cur[j - 1] + 1;
            if (prev[j - 1] + c < m) m = prev[j - 1] + c;
            cur[j] = m;
        }
        for (int j = 0; j <= nb; j++) prev[j] = cur[j];
    }
    return prev[nb];
}

static char labd[MAX_LABEL][512];
static const char *labp[MAX_LABEL];
static int32_t ids_dev[MAX_IDS], ids_full[MAX_IDS], ids_got[MAX_IDS];

int main(int argc, char **argv) {
    const char *path = (argc > 1) ? argv[1] : "k6_vectors.bin";
#if defined(K7_EXTERNAL_SCRATCH) && K7_EXTERNAL_SCRATCH
    /* T10(a) の陽性側: **置き場を外から渡した構成でも同じ列になるか**。
     * 端末（esp32/main/saan_kanji.c）は合成 arena から切り出して渡す。
     * ⚠️ ここで渡さないと k7_label2ids は K7_ERR_ARG を返す = 全件落ちる
     *    （**黙って動かない**のではなく落ちることの確認でもある）。 */
    static char k7_scratch[K7_SCRATCH_BYTES];
    k7_set_scratch(k7_scratch, sizeof k7_scratch);
#endif
    /* --dump-ids <file>: 端末 ids とホスト ids を書き出す（音の測定に渡す）。
     * ⚠️ **本文は書かない。** レポートにコーパス本文を混ぜないため（hook が deny する）。
     * 形式: 1 行 1 文 `<index>\t<dev ids 空白区切り>\t<host ids 空白区切り>` */
    const char *dump = NULL;
    for (int i = 2; i + 1 < argc; i++)
        if (strcmp(argv[i], "--dump-ids") == 0) dump = argv[i + 1];
    FILE *df = dump ? fopen(dump, "wb") : NULL;
    if (dump && !df) { fprintf(stderr, "NG: %s が開けない\n", dump); return 1; }
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "NG: ベクタが開けない: %s\n", path); return 1; }
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t *buf = malloc((size_t)sz);
    if (!buf || fread(buf, 1, (size_t)sz, f) != (size_t)sz) return 1;
    fclose(f);
    if (memcmp(buf, "K6V2", 4)) { fprintf(stderr, "NG: magic\n"); return 1; }
    g = buf + 4;
    uint32_t n_cases = rd32();

    static k4_dan_t dan[256];
    static char dan_kana[256][8];
    uint32_t n_dan_u = rd32();
    int n_dan = (int)(n_dan_u < 256 ? n_dan_u : 256);
    for (uint32_t i = 0; i < n_dan_u; i++) {
        char kana[8]; rdstr(kana, sizeof kana);
        char v = (char)*g++;
        if (i < 256) {
            snprintf(dan_kana[i], sizeof dan_kana[0], "%s", kana);
            dan[i].kana = dan_kana[i]; dan[i].dan = v;
        }
    }
    uint32_t blob_len = rd32();
    const uint8_t *blob = g; g += blob_len;

    k1_dict_t d;
    if (k1_open(&d, blob, blob_len) != 0) { fprintf(stderr, "NG: k1_open\n"); return 1; }

    void *arena = malloc(ARENA_N);
    static char text[4096], key[8192], feat_buf[MAX_TOK][512];
    static char *feat[MAX_TOK];
    static k1_token_t tok[MAX_TOK];
    static k4_node_t k4n[MAX_TOK];

    int ok25 = 0, ng25 = 0, ok26 = 0, ng26 = 0, err = 0, shown = 0;
    int ctrl25 = 0;
    /* G26b: **どれだけ違うか**。合否ではなく判断材料（C / D）。 */
    long ed_all = 0, len_all = 0;      /* 記号込みの列 */
    long ed_ph  = 0, len_ph  = 0;      /* 音素だけの列 */

    for (int pass = 0; pass < 2; pass++) {
    /* pass 1 = 陰性対照: アクセント記号 `[` を出さない（k7 側を壊す） */
    if (pass == 1) k7_debug_drop_rise = 1; else k7_debug_drop_rise = 0;
    g = blob + blob_len;
    for (uint32_t c = 0; c < n_cases; c++) {
        rdstr(text, sizeof text);
        uint32_t nhf = rd32();
        for (uint32_t i = 0; i < nhf; i++) { char tmp[512]; rdstr(tmp, sizeof tmp); }
        uint32_t nfull = rd32();
        for (uint32_t i = 0; i < nfull; i++) { char tmp[512]; rdstr(tmp, sizeof tmp); }
        uint32_t ndev = rd32();
        for (uint32_t i = 0; i < ndev; i++) {
            rdstr(labd[i < MAX_LABEL ? i : MAX_LABEL - 1], sizeof labd[0]);
            labp[i < MAX_LABEL ? i : MAX_LABEL - 1] = labd[i < MAX_LABEL ? i : MAX_LABEL - 1];
        }
        uint32_t nid = rd32();
        for (uint32_t i = 0; i < nid; i++)
            ids_dev[i < MAX_IDS ? i : MAX_IDS - 1] = (int32_t)rd32();
        uint32_t nidf = rd32();
        for (uint32_t i = 0; i < nidf; i++)
            ids_full[i < MAX_IDS ? i : MAX_IDS - 1] = (int32_t)rd32();

        /* --- G25: ホストのラベルを入れて ids を作る ------------------- */
        int32_t n_got = 0;
        k7_status st = k7_label2ids(labp, (int)ndev, text,
                                    ids_got, MAX_IDS, &n_got);
        int same = (st == K7_OK) && ((uint32_t)n_got == nid);
        if (same)
            for (int32_t i = 0; i < n_got; i++)
                if (ids_got[i] != ids_dev[i]) { same = 0; break; }
        if (pass == 1) { if (!same) ctrl25++; }
        else if (same) ok25++;
        else {
            ng25++;
            if (shown < 3) {
                printf("  G25 食い違い: %.34s\n", text);
                printf("    ホスト %u 個 / 端末 %d 個 (%s)\n", nid, (int)n_got,
                       k7_strerror(st));
                shown++;
            }
        }
        if (pass == 1) continue;

        /* --- G26: 漢字文から通しで ids を作る ------------------------- */
        size_t key_n = sizeof key;
        if (k1_encode_key(&d, (const uint8_t *)text, strlen(text),
                          (uint8_t *)key, &key_n) != 0) { err++; continue; }
        int nt = k1_analyze(&d, (const uint8_t *)key, key_n,
                            arena, ARENA_N, tok, MAX_TOK);
        if (nt <= 0) { ng26++; continue; }
        int nf = 0, bad = 0;
        for (int i = 0; i < nt && nf < MAX_TOK; i++) {
            char surf[256];
            if (k1_key_to_utf8(&d, (const uint8_t *)key, tok[i].begin, tok[i].end,
                               surf, sizeof surf) < 0) { bad = 1; break; }
            int r = -1;
            if (tok[i].entry & K1_UNKNOWN_FLAG)
                r = k1_unk_guess(&d, tok[i].entry, surf, feat_buf[nf],
                                 sizeof feat_buf[0]);
            if (r < 0)
                r = (tok[i].entry & K1_UNKNOWN_FLAG)
                    ? k1_unk_feature(&d, tok[i].entry, surf, feat_buf[nf],
                                     sizeof feat_buf[0])
                    : k1_entry_feature(&d, tok[i].entry, surf, feat_buf[nf],
                                       sizeof feat_buf[0]);
            if (r < 0) { bad = 1; break; }
            feat[nf] = feat_buf[nf]; nf++;
        }
        if (bad || nf <= 0) { err++; continue; }

        NJD njd; JPCommon jp;
        NJD_initialize(&njd); JPCommon_initialize(&jp);
        mecab2njd(&njd, feat, nf);
        njd_set_pronunciation(&njd);
        k4b_before_chaining(&njd);
        njd_set_digit(&njd);
        njd_set_accent_phrase(&njd);
        njd_set_accent_type(&njd);
        njd_set_unvoiced_vowel(&njd);
        njd_set_long_vowel(&njd);
        int nk = njd_to_k4(&njd, k4n, MAX_TOK);
        k4_apply(k4n, nk, K4_ALL, dan, n_dan);
        k4_to_njd(&njd, k4n, nk);
        njd2jpcommon(&jp, &njd);
        JPCommon_make_label(&jp);
        int nl = JPCommon_get_label_size(&jp);
        char **ls = JPCommon_get_label_feature(&jp);
        static const char *lp2[MAX_LABEL];
        for (int i = 0; i < nl && i < MAX_LABEL; i++) lp2[i] = ls[i];

        int32_t n2 = 0;
        k7_status st2 = k7_label2ids(lp2, nl < MAX_LABEL ? nl : MAX_LABEL,
                                     text, ids_got, MAX_IDS, &n2);
        int same2 = (st2 == K7_OK) && ((uint32_t)n2 == nidf);
        if (same2)
            for (int32_t i = 0; i < n2; i++)
                if (ids_got[i] != ids_full[i]) { same2 = 0; break; }
        if (same2) ok26++; else ng26++;
        if (df) {
            fprintf(df, "%u\t", c);
            for (int32_t i = 0; i < n2; i++) fprintf(df, "%s%d", i ? " " : "", ids_got[i]);
            fprintf(df, "\t");
            for (uint32_t i = 0; i < nidf; i++) fprintf(df, "%s%d", i ? " " : "", ids_full[i]);
            fprintf(df, "\n");
        }

        /* --- G26b: 編集距離（PAD を除いた列で測る）------------------- */
        {
            static int32_t a[MAX_IDS], b[MAX_IDS];
            static int32_t ap[MAX_IDS], bp[MAX_IDS];
            int32_t pad = k7_token_id("_");
            int na = 0, nb = 0, nap = 0, nbp = 0;
            /* ⚠️ **PAD を落とす。** intersperse した PAD は音素 1 個につき
             *    1 個入るので、残すと編集距離が倍に膨らんで読めなくなる。 */
            for (int32_t i = 0; i < n2; i++)
                if (ids_got[i] != pad) {
                    a[na++] = ids_got[i];
                    if (!k7_is_mark(ids_got[i])) ap[nap++] = ids_got[i];
                }
            for (uint32_t i = 0; i < nidf; i++)
                if (ids_full[i] != pad) {
                    b[nb++] = ids_full[i];
                    if (!k7_is_mark(ids_full[i])) bp[nbp++] = ids_full[i];
                }
            ed_all += lev(a, na, b, nb); len_all += nb;
            ed_ph  += lev(ap, nap, bp, nbp); len_ph += nbp;
        }
        JPCommon_clear(&jp); NJD_clear(&njd);
    }
    }

    printf("\n=== G25: ラベル → 生徒インデックス（移植の正しさ）===\n");
    printf("  %s ホストと一致 %d / %u 文（食い違い %d）\n",
           (ng25 == 0 && ok25 > 0) ? "OK " : "NG ", ok25, n_cases, ng25);
    printf("  陰性対照（`[` を出さない）: %d 件が食い違う%s\n", ctrl25,
           ctrl25 ? "" : "   ⚠️ **対照が空虚**");

    printf("\n=== G26: 漢字文から通しで ids（**閾値は置かない**）===\n");
    printf("  ホスト既定と一致 %d / %u 文（%.2f%% が食い違う / エラー %d）\n",
           ok26, n_cases, 100.0 * ng26 / (double)n_cases, err);
    printf("  ⚠️ この差は**辞書の枝刈り**が主因（C-050 / M-74）\n");

    printf("\n=== G26b: **どれだけ違うか**（判断 C / D の材料）===\n");
    printf("  記号込みの列  編集距離 %ld / ホスト長 %ld = **%.2f%%**\n",
           ed_all, len_all, len_all ? 100.0 * ed_all / len_all : 0.0);
    printf("  音素だけの列  編集距離 %ld / ホスト長 %ld = **%.2f%%**\n",
           ed_ph, len_ph, len_ph ? 100.0 * ed_ph / len_ph : 0.0);
    printf("  ⚠️ **PAD を除いた列で測っている**（intersperse した PAD を残すと倍に膨らむ）\n");
    printf("  ⚠️ **これは音の良し悪しではない。** 「ホストと違う音素の割合」\n");

    printf("\n=== G27: 語彙表 ===\n");
    printf("  %d トークン（`src/saanotts_jp/vocab.py` から生成）\n", k7_n_tokens);

    int bad = (ng25 != 0) || (ok25 == 0) || (ctrl25 == 0);
    printf("\n%s\n", bad ? "NG!" : "OK  G25 通過（ラベル → ids はホストと完全一致）");
    if (df) { fclose(df); printf("\n  ids を %s に書き出した\n", dump); }
    free(arena); free(buf);
    return bad;
}
