/* K-6 の受け入れゲート。   make -C csrc k6
 *
 * 端末の全段を 1 本に繋いでホストと比べる:
 *
 *   漢字文 → k1_encode_key → k1_analyze（K-2/K-3）
 *          → k1_entry_feature → mecab2njd
 *          → njd_set_pronunciation → k4b_before_chaining → njd_set_digit
 *          → njd_set_accent_phrase → njd_set_accent_type
 *          → njd_set_unvoiced_vowel → njd_set_long_vowel        （K-4b）
 *          → k4_apply（K-4 の 4 段）
 *          → njd2jpcommon → JPCommon_make_label
 *
 * G17   ホストの**端末に載る段だけ**の結果と、フルコンテキストラベルが一致する
 *       （食い違いが D-043 で許容した 0.60% 以下）
 * G17b  食い違いの内訳を出す（率だけだと別原因で同じ率になっても通る）
 * G17c  参考: ホスト既定（Sudachi / ONNX 込み）との差も併記する
 *
 * ⚠️ **ホストはフル辞書、端末は枝刈り辞書。** 差の主因はそこ。
 */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "k1dict.h"
#include "k4_accent.h"
#include "k4b_njd.h"
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
#define ARENA_N   (4u << 20)

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

/* feature 文字列の idx 番目のフィールド。0 で成功、無ければ非 0。 */
static int field_of(const char *feat, int idx, char *out, size_t out_n) {
    int k = 0; size_t o = 0;
    for (const char *q = feat; ; q++) {
        if (*q == ',' || *q == 0) {
            if (k == idx) { if (o >= out_n) return -1; out[o] = 0; return 0; }
            k++; o = 0;
            if (*q == 0) return -1;
            continue;
        }
        if (k == idx) { if (o + 1 >= out_n) return -1; out[o++] = *q; }
    }
}

/* NJD ↔ k4_node_t の橋渡し。K-4 は NJD を知らない構造体で書いてある。 */
static int njd_to_k4(NJD *njd, k4_node_t *out, int max_out) {
    int n = 0;
    for (NJDNode *p = njd->head; p && n < max_out; p = p->next, n++) {
        snprintf(out[n].pos,   K4_STR_MAX,  "%s", NJDNode_get_pos(p));
        snprintf(out[n].ctype, K4_STR_MAX,  "%s", NJDNode_get_ctype(p));
        snprintf(out[n].cform, K4_STR_MAX,  "%s", NJDNode_get_cform(p));
        snprintf(out[n].orig,  K4_STR_MAX,  "%s", NJDNode_get_orig(p));
        snprintf(out[n].pron,  K4_PRON_MAX, "%s", NJDNode_get_pron(p));
        snprintf(out[n].read,  K4_PRON_MAX, "%s", NJDNode_get_read(p));
        out[n].acc        = NJDNode_get_acc(p);
        out[n].mora_size  = NJDNode_get_mora_size(p);
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

int main(int argc, char **argv) {
    const char *path = (argc > 1) ? argv[1] : "k6_vectors.bin";
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "NG: ベクタが開けない: %s\n", path); return 1; }
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t *buf = malloc((size_t)sz);
    if (!buf || fread(buf, 1, (size_t)sz, f) != (size_t)sz) return 1;
    fclose(f);
    if (memcmp(buf, "K6V2", 4)) { fprintf(stderr, "NG: magic\n"); return 1; }
    g = buf + 4;
    uint32_t n_cases = rd32();

    /* _DAN_MAP（K-4 の suppress_u が使う唯一の外部資源。かな 76 件） */
    static k4_dan_t dan[256];
    static char dan_kana[256][8];
    uint32_t n_dan_u = rd32();
    int n_dan = (int)(n_dan_u < 256 ? n_dan_u : 256);
    for (uint32_t i = 0; i < n_dan_u; i++) {
        char kana[8];
        rdstr(kana, sizeof kana);
        char v = (char)*g++;
        if (i < 256) {
            snprintf(dan_kana[i], sizeof dan_kana[0], "%s", kana);
            dan[i].kana = dan_kana[i];
            dan[i].dan = v;
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

    int ok_dev = 0, ng_dev = 0, ok_full = 0, err = 0;
    int n_len_diff = 0, n_tok_fail = 0, n_unk_sent = 0;
    int shown = 0, shown_feat = 0, shown_unk = 0;
    int n_host_knew = 0, n_guess_right = 0, n_unk_both = 0;
    int n_host_tok = 0, n_host_unk = 0, n_dev_tok = 0;

    static char labf[MAX_LABEL][512], labd[MAX_LABEL][512];
    static char hfeat[MAX_TOK][512];
    int n_feat_same = 0, n_feat_diff = 0, n_lab_diff_feat_same = 0;

    /* pass 0 = 本番（フォールバック有）
     * pass 1 = 陰性対照（K-4 の 4 段を抜く）
     * pass 2 = 対照（未知語のフォールバックを抜く = 無音で消える） */
    const uint8_t *body = g;
    int ctrl_diff = 0;
    int n_unk_tok[3] = {0, 0, 0}, n_guessed[3] = {0, 0, 0};
    int ok_nofb = 0;
    for (int pass = 0; pass < 3; pass++) {
    int no_k4 = (pass == 1);
    int fallback = (pass != 2);
    int n_unk_tok_pass = 0, n_guessed_pass = 0;
    g = body;
    for (uint32_t c = 0; c < n_cases; c++) {
        rdstr(text, sizeof text);
        uint32_t nhf = rd32();
        for (uint32_t i = 0; i < nhf; i++) {
            rdstr(hfeat[i < MAX_TOK ? i : MAX_TOK - 1], sizeof hfeat[0]);
            if (pass == 0) {
                char tmp[64];
                n_host_tok++;
                /* 10 列目（発音）が無い = ホストにとっても未知語 */
                if (field_of(hfeat[i < MAX_TOK ? i : MAX_TOK - 1], 9,
                             tmp, sizeof tmp) != 0) n_host_unk++;
            }
        }
        uint32_t nfull = rd32();
        for (uint32_t i = 0; i < nfull; i++)
            rdstr(labf[i < MAX_LABEL ? i : MAX_LABEL - 1], sizeof labf[0]);
        uint32_t ndev = rd32();
        for (uint32_t i = 0; i < ndev; i++)
            rdstr(labd[i < MAX_LABEL ? i : MAX_LABEL - 1], sizeof labd[0]);
        /* ⚠️ **ids は必ずここで読む。** 下に `continue` が何本もあるので、
         *    後ろで読むとストリームがずれて残り全部が壊れる。 */
        for (int k = 0; k < 2; k++) { uint32_t ni = rd32(); g += 4u * ni; }

        /* --- 端末側を走らせる ------------------------------------------- */
        size_t key_n = sizeof key;
        if (k1_encode_key(&d, (const uint8_t *)text, strlen(text),
                          (uint8_t *)key, &key_n) != 0) { if (pass == 0) err++; continue; }
        int nt = k1_analyze(&d, (const uint8_t *)key, key_n,
                            arena, ARENA_N, tok, MAX_TOK);
        if (nt <= 0) { if (pass == 0) { n_tok_fail++; ng_dev++; } continue; }

        int nf = 0, has_unk = 0, bad = 0;
        for (int i = 0; i < nt && nf < MAX_TOK; i++) {
            char surf[256];
            if (k1_key_to_utf8(&d, (const uint8_t *)key, tok[i].begin, tok[i].end,
                               surf, sizeof surf) < 0) { bad = 1; break; }
            int r;
            if (tok[i].entry & K1_UNKNOWN_FLAG) {
                has_unk = 1;
                n_unk_tok_pass++;
                r = -1;
                if (fallback) {
                    r = k1_unk_guess(&d, tok[i].entry, surf,
                                     feat_buf[nf], sizeof feat_buf[0]);
                    if (r >= 0) {
                        n_guessed_pass++;
                        /* ⚠️ **推測が当たったかを直接見る。**
                         * ホストの feature 列に同じ表層があり、12 列（=
                         * ホストは知っている語）なら、発音を突き合わせる。 */
                        if (pass == 0) {
                            char hs[256], hp[256], gp[256];
                            for (uint32_t k = 0; k < nhf; k++) {
                                if (field_of(hfeat[k], 0, hs, sizeof hs) != 0) continue;
                                if (strcmp(hs, surf)) continue;
                                if (field_of(hfeat[k], 9, hp, sizeof hp) != 0) {
                                    n_unk_both++;      /* ホストも知らない */
                                    break;
                                }
                                n_host_knew++;
                                if (field_of(feat_buf[nf], 9, gp, sizeof gp) == 0
                                    && strcmp(gp, hp) == 0) n_guess_right++;
                                break;
                            }
                        }
                    }
                }
                if (r < 0) {    /* 推測できない → 8 列のまま = **無音で消える** */
                    if (pass == 0 && shown_unk < 8) {
                        printf("  推測できず（%d）: %s\n", r, surf);
                        shown_unk++;
                    }
                    r = k1_unk_feature(&d, tok[i].entry, surf,
                                       feat_buf[nf], sizeof feat_buf[0]);
                }
            } else {
                r = k1_entry_feature(&d, tok[i].entry, surf,
                                     feat_buf[nf], sizeof feat_buf[0]);
            }
            if (r < 0) { bad = 1; break; }
            feat[nf] = feat_buf[nf];
            nf++;
        }
        if (bad || nf <= 0) { if (pass == 0) { err++; ng_dev++; } continue; }
        if (pass == 0) n_dev_tok += nf;
        if (has_unk && pass == 0) n_unk_sent++;

        /* ⚠️ **素性の一致を先に見る。** ここが違えば原因は辞書の枝刈り側で、
         *    NJD / K-4 の移植の話ではない。 */
        int feat_same = ((uint32_t)nf == nhf);
        if (feat_same)
            for (int i = 0; i < nf; i++)
                if (strcmp(feat[i], hfeat[i])) { feat_same = 0; break; }
        if (feat_same) { if (pass == 0) n_feat_same++; }
        else {
            if (pass == 0) n_feat_diff++;
            if (no_k4) { /* 対照では素性差の表示はしない */ }
            else
            if (shown_feat < 4) {
                for (int i = 0; i < nf && i < (int)nhf; i++)
                    if (strcmp(feat[i], hfeat[i])) {
                        printf("  素性差: ホスト %s\n         端末   %s\n",
                               hfeat[i], feat[i]);
                        break;
                    }
                if ((uint32_t)nf != nhf)
                    printf("  素性の件数差: ホスト %u / 端末 %d（%.30s）\n",
                           nhf, nf, text);
                shown_feat++;
            }
        }

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
        k4_apply(k4n, nk, no_k4 ? 0u : K4_ALL, dan, n_dan);
        k4_to_njd(&njd, k4n, nk);
        njd2jpcommon(&jp, &njd);
        JPCommon_make_label(&jp);

        int nl = JPCommon_get_label_size(&jp);
        char **ls = JPCommon_get_label_feature(&jp);

        int same_dev = ((uint32_t)nl == ndev);
        if (same_dev)
            for (int i = 0; i < nl; i++)
                if (strcmp(ls[i], labd[i])) { same_dev = 0; break; }
        int same_full = ((uint32_t)nl == nfull);
        if (same_full)
            for (int i = 0; i < nl; i++)
                if (strcmp(ls[i], labf[i])) { same_full = 0; break; }

        if (pass == 1) {
            if (feat_same && !same_dev) ctrl_diff++;
            JPCommon_clear(&jp); NJD_clear(&njd);
            continue;
        }
        if (pass == 2) {
            if (same_dev) ok_nofb++;
            JPCommon_clear(&jp); NJD_clear(&njd);
            continue;
        }
        if (same_dev) ok_dev++; else ng_dev++;
        if (!same_dev && feat_same) n_lab_diff_feat_same++;
        if (same_full) ok_full++;
        if (!same_dev) {
            if ((uint32_t)nl != ndev && pass == 0) n_len_diff++;
            if (shown < 3) {
                printf("  食い違い: %.40s\n", text);
                printf("    ホスト %u 本 / 端末 %d 本%s\n", ndev, nl,
                       has_unk ? " / 未知語あり" : "");
                shown++;
            }
        }
        JPCommon_clear(&jp); NJD_clear(&njd);
    }
    n_unk_tok[pass] = n_unk_tok_pass;
    n_guessed[pass] = n_guessed_pass;
    }

    double pct = 100.0 * ng_dev / (double)n_cases;
    double pct_feat = 100.0 * n_feat_diff / (double)n_cases;

    printf("\n=== G17: 移植の正しさ — **素性が一致した文でラベルが一致するか** ===\n");
    printf("  %s 素性が一致した %d 文のうち、ラベルが食い違ったのは **%d 件**\n",
           (n_lab_diff_feat_same == 0 && n_feat_same > 0) ? "OK " : "NG ",
           n_feat_same, n_lab_diff_feat_same);
    printf("  陰性対照（K-4 の 4 段を抜く）: %d 件が食い違う%s\n", ctrl_diff,
           ctrl_diff ? "" : "   ⚠️ **対照が空虚**");

    printf("\n=== G17b: 辞書の枝刈りの代償（**閾値は置かない**）===\n");
    printf("  MeCab feature 列がホストと一致  %d / %u（%.2f%% が食い違う）\n",
           n_feat_same, n_cases, pct_feat);
    printf("  ラベルまで含めた一致            %d / %u（%.2f%% が食い違う）\n",
           ok_dev, n_cases, pct);
    printf("  ラベル本数が違う %d / トークン列が取れない %d / 未知語を含む文 %d\n",
           n_len_diff, n_tok_fail, n_unk_sent);
    printf("  ⚠️ **D-043 の「0.60%%」はここには使えない。** あれは同形異音語\n");
    printf("     （Sudachi）の 14 文から出た数で、**枝刈りの代償ではない**（C-050）\n");

    printf("\n=== G17d: 未知語のフォールバック ===\n");
    printf("  トークン: ホスト %d / 端末 %d\n", n_host_tok, n_dev_tok);
    printf("  **ホストにとっても未知語** %d 件（%.2f%%）\n", n_host_unk,
           n_host_tok ? 100.0 * n_host_unk / n_host_tok : 0.0);
    printf("  端末の未知語トークン %d 件（%.2f%%）\n", n_unk_tok[0],
           n_dev_tok ? 100.0 * n_unk_tok[0] / n_dev_tok : 0.0);
    printf("  ⚠️ **枝刈りで落ちた語は未知語にならない。** より短い既知語に\n");
    printf("     切り直される（例: 上毛 → 上 + 毛）。壊れ方は"
           "**無音消滅ではなく誤読**\n");
    printf("  うち読みを推測できた %d 件（%.1f%%）"
           " ← **残りは無音で消える**\n", n_guessed[0],
           n_unk_tok[0] ? 100.0 * n_guessed[0] / n_unk_tok[0] : 0.0);
    printf("  うちホストは知っていた語 %d 件 → **発音が当たった %d 件（%.1f%%）**\n",
           n_host_knew, n_guess_right,
           n_host_knew ? 100.0 * n_guess_right / n_host_knew : 0.0);
    printf("  ホストも知らない語        %d 件"
           "（ホストは無音で消す。合わせに行く意味は無い）\n", n_unk_both);
    printf("  対照（フォールバック無し）でのラベル一致 %d / %u\n",
           ok_nofb, n_cases);
    printf("  フォールバック有りでのラベル一致       %d / %u\n", ok_dev, n_cases);
    printf("  ⚠️ **一致率で測れるのは「たまたま当たった」分だけ。**\n");
    printf("     無音消滅を避けた分は**音でしか判断できない**（聴取は未実施）\n");

    printf("\n=== G17c: 参考 — ホスト既定（Sudachi / ONNX 込み）との一致 ===\n");
    printf("  一致 %d / %u 文（%.2f%%）\n", ok_full, n_cases,
           100.0 * ok_full / (double)n_cases);
    printf("  ⚠️ **この差は移植の誤りではない。** 端末に載らない 3 段の分（M-70）\n");

    int bad = (n_lab_diff_feat_same != 0) || (n_feat_same == 0) || (ctrl_diff == 0)
            || (err != 0);
    printf("\n%s\n", bad ? "NG!" : "OK  G17 通過（移植は素性が同じなら完全一致）");
    free(arena); free(buf);
    return bad;
}
