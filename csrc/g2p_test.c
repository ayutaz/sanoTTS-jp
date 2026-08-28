/* 端末側 G2P の受け入れゲート。
 *
 *   ./g2p_test g2p_vectors.bin [--min-vectors N]
 *
 * ベクタは `scripts/gen_g2p_vectors.py` が `scripts/kana_g2p.py` を**正**として吐く。
 * 判定は**整数列の完全一致**。相関・一致率のような「不変量を持つ指標」は使わない
 * （Pearson だけを合否にしていた golden_test が層落ちを検出できなかった前例がある）。
 *
 * 空虚に通らないようにするための仕掛け:
 *
 *   G0  ベクタファイルに依存しない**既知解**（esp32/main/demo_ids.h の 53 ids）と、
 *       **陰性対照**（1 文字変えたら ids が変わること）。ファイルが壊れていても
 *       「0 件中 0 件一致 = OK」にならない
 *   G1  テーブルの SHA-256 一致。**違うテーブルで作ったベクタとの突き合わせ**を弾く
 *   G2  ベクタ件数の下限。空ファイル・生成失敗で満点を取らせない
 *   G7  交差比較。別ベクタの期待 ids を当てて**必ず不一致になる**ことを確認する
 *       （比較関数が「常に一致」を返していないことの証明）
 *
 * ⚠️ **allow 側（正常入力）と deny 側（エラー入力）を両方持つ。** deny だけだと
 *    「全部エラーを返す」実装で満点が取れる。逆も同じ。
 */
#include "g2p.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* --- ベクタファイル ------------------------------------------------------ */

#define VEC_MAGIC   "G2PV"
#define VEC_VERSION 1u
#define KIND_OK           0u
#define KIND_ERR_UNKNOWN  1u
#define KIND_ERR_UTF8     2u

typedef struct {
    uint32_t kind;
    int32_t  err_byte;
    uint32_t text_len, n_ids, n_phonemes, n_pad, n_drop_long, n_drop_devoice;
    const char          *name;   /* NUL 終端ではない。長さは name_len */
    uint32_t             name_len;
    const unsigned char *text;
    const int32_t       *ids;
} vec_t;

static unsigned char *slurp(const char *path, size_t *size) {
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "開けない: %s\n", path); exit(2); }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (n <= 0) { fprintf(stderr, "空のファイル: %s\n", path); exit(2); }
    unsigned char *b = (unsigned char *)malloc((size_t)n);
    if (!b || fread(b, 1, (size_t)n, f) != (size_t)n) {
        fprintf(stderr, "読めない: %s\n", path); exit(2);
    }
    fclose(f);
    *size = (size_t)n;
    return b;
}

static uint32_t rd_u32(const unsigned char *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8)
         | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/* 名前を表示用に切り出す（NUL 終端ではないので毎回コピーする） */
static const char *vname(const vec_t *v) {
    static char buf[128];
    size_t n = v->name_len < sizeof buf - 1 ? v->name_len : sizeof buf - 1;
    memcpy(buf, v->name, n);
    buf[n] = '\0';
    return buf;
}

/* 入力を 16 進 + 可読文字で出す。**不正 UTF-8 も含むので端末にそのまま流さない** */
static void dump_text(const unsigned char *t, uint32_t n) {
    printf("      入力 %u B: ", n);
    for (uint32_t i = 0; i < n && i < 48u; ++i) printf("%02x ", t[i]);
    if (n > 48u) printf("...");
    printf("\n           ");
    for (uint32_t i = 0; i < n && i < 48u; ++i)
        printf("%c", (t[i] >= 0x20 && t[i] < 0x7f) ? (char)t[i] : '.');
    printf("\n");
}

static void dump_ids(const char *label, const int32_t *ids, int32_t n,
                     int32_t around) {
    const int32_t lo = around > 4 ? around - 4 : 0;
    const int32_t hi = (around + 5 < n) ? around + 5 : n;
    printf("      %s[%d..%d):", label, lo, hi);
    for (int32_t i = lo; i < hi; ++i) printf(" %d", ids[i]);
    printf("   (全 %d 個)\n", n);
}

/* --- 出力バッファ（オーバーラン検出つき）--------------------------------- */

#define GUARD_N     16          /* ids バッファの後ろに置く番兵 */
#define POISON      0x7f5a5a5aL /* 未書き込みを見分けるための毒 */

typedef struct {
    int32_t *buf;      /* [cap + GUARD_N] */
    int32_t  cap;
} obuf_t;

static void obuf_poison(obuf_t *o) {
    for (int32_t i = 0; i < o->cap + GUARD_N; ++i) o->buf[i] = (int32_t)POISON;
}

/* 番兵が無傷か。**バッファ 1 個ぶんの書き過ぎ**を捕まえる */
static int obuf_guard_ok(const obuf_t *o, int32_t used_cap) {
    for (int32_t i = used_cap; i < o->cap + GUARD_N; ++i)
        if (o->buf[i] != (int32_t)POISON) return 0;
    return 1;
}

/* --- G0: ベクタファイルに依存しない既知解 -------------------------------- */

/* 「今日は良い天気ですね。」の中間表現。**esp32/main/demo_ids.h と同じ 53 ids** に
 * なることが Python 側で確認済み（`scripts/gen_g2p_vectors.py` の edge:demo_sentence）。
 * ⚠️ ここはベクタファイルを一切読まないので、**ファイルが壊れていても検出できる**。 */
static const char  kG0Text[] = "きょ][おわよ][いて]["
                               "んきです°ね";
#define G0_TEXT_BYTES 44
static const int32_t kG0Ids[53] = {
     1,  0, 26,  0, 14,  0,  9,  0,  8,  0, 14,  0,
    55,  0, 10,  0, 56,  0, 14,  0,  9,  0,  8,  0,
    11,  0, 31,  0, 13,  0,  9,  0,  8,  0, 22,  0,
    25,  0, 11,  0, 33,  0, 13,  0, 41,  0, 17,  0,
    49,  0, 13,  0,  2,
};
/* 陰性対照: 末尾 `ね` を `の` に変えた 1 文字違い。**ids が変われば**比較が
 * 効いていることの証明になる。変わらなければテストは空虚（毒を入れても緑）。 */
static const char kG0Mutant[] = "きょ][おわよ][いて]["
                                "んきです°の";

static int gate0(void) {
    int bad = 0;
    printf("G0 既知解と陰性対照（ベクタファイルに依存しない）\n");

    if (sizeof kG0Text - 1 != G0_TEXT_BYTES) {
        printf("  NG! 既知解の入力長が %zu B（%d B のはず）— ソースの符号化を疑う\n",
               sizeof kG0Text - 1, G0_TEXT_BYTES);
        return 1;
    }

    int32_t got[128 + GUARD_N];
    obuf_t o = {got, 128};
    obuf_poison(&o);
    int32_t n = -1;
    saan_g2p_info info;
    saan_g2p_status s = saan_g2p(kG0Text, sizeof kG0Text - 1, got, 128, &n, &info);
    if (s != SAAN_G2P_OK) {
        printf("  NG! 既知解が失敗した: %s (err_byte=%d)\n",
               saan_g2p_strerror(s), info.err_byte);
        ++bad;
    } else if (n != 53) {
        printf("  NG! 既知解の ids 個数が %d（53 のはず）\n", n);
        ++bad;
    } else {
        int32_t first = -1;
        for (int32_t i = 0; i < 53; ++i)
            if (got[i] != kG0Ids[i]) { first = i; break; }
        if (first >= 0) {
            printf("  NG! 既知解が demo_ids.h と一致しない（最初のずれ i=%d）\n", first);
            dump_ids("期待", kG0Ids, 53, first);
            dump_ids("実際", got, 53, first);
            ++bad;
        } else {
            printf("  OK  既知解 53 ids が demo_ids.h と完全一致\n");
        }
    }
    if (!obuf_guard_ok(&o, n > 0 ? n : 0)) {
        printf("  NG! 既知解で n_ids より後ろに書き込んだ\n");
        ++bad;
    }

    /* 陰性対照は「基準が通っている」ことが前提。基準が落ちている間は空虚なので、
     * そのことを明示する（ここで OK と出しても意味が無い） */
    int32_t mut[128 + GUARD_N];
    obuf_t om = {mut, 128};
    obuf_poison(&om);
    int32_t nm = -1;
    saan_g2p_status sm = saan_g2p(kG0Mutant, sizeof kG0Mutant - 1, mut, 128, &nm, NULL);
    const int differs = (sm != SAAN_G2P_OK) || (nm != 53)
                     || (memcmp(mut, kG0Ids, sizeof kG0Ids) != 0);
    if (!differs) {
        printf("  NG! 陰性対照: 1 文字変えても ids が同じ — 比較が効いていない\n");
        ++bad;
    } else if (bad) {
        printf("  --  陰性対照は基準が落ちている間は無意味（基準を先に通すこと）\n");
    } else {
        printf("  OK  陰性対照: 1 文字変えると ids が変わる\n");
    }
    return bad;
}

/* --- main ---------------------------------------------------------------- */

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s g2p_vectors.bin [--min-vectors N]\n", argv[0]);
        return 2;
    }
    long min_vectors = 2700;
    for (int i = 2; i < argc; ++i)
        if (strcmp(argv[i], "--min-vectors") == 0 && i + 1 < argc)
            min_vectors = strtol(argv[++i], NULL, 10);

    size_t fsz = 0;
    unsigned char *fb = slurp(argv[1], &fsz);
    if (fsz < 48 || memcmp(fb, VEC_MAGIC, 4) != 0) {
        fprintf(stderr, "NG! %s はベクタファイルではない\n", argv[1]);
        return 1;
    }
    const uint32_t version = rd_u32(fb + 4);
    const uint32_t n_vec   = rd_u32(fb + 8);
    const unsigned char *file_sha = fb + 16;
    if (version != VEC_VERSION) {
        fprintf(stderr, "NG! ベクタ形式 v%u（v%u を期待）\n", version, VEC_VERSION);
        return 1;
    }

    /* 全レコードを走査してテーブル化する（可変長なので 1 パス目で索引を作る） */
    vec_t *V = (vec_t *)calloc(n_vec ? n_vec : 1, sizeof(vec_t));
    size_t off = 48;
    uint32_t max_ids = 0, max_text = 0;
    for (uint32_t k = 0; k < n_vec; ++k) {
        if (off + 40 > fsz) { fprintf(stderr, "NG! 途中で切れている (%u)\n", k); return 1; }
        V[k].kind           = rd_u32(fb + off + 0);
        V[k].err_byte       = (int32_t)rd_u32(fb + off + 4);
        V[k].text_len       = rd_u32(fb + off + 8);
        V[k].n_ids          = rd_u32(fb + off + 12);
        V[k].n_phonemes     = rd_u32(fb + off + 16);
        V[k].n_pad          = rd_u32(fb + off + 20);
        V[k].n_drop_long    = rd_u32(fb + off + 24);
        V[k].n_drop_devoice = rd_u32(fb + off + 28);
        V[k].name_len       = rd_u32(fb + off + 32);
        off += 40;
        V[k].name = (const char *)(fb + off);            off += V[k].name_len;
        V[k].text = fb + off;                            off += V[k].text_len;
        off = (off + 3u) & ~(size_t)3u;                  /* ids を 4 B 境界へ */
        V[k].ids = (const int32_t *)(const void *)(fb + off);
        off += (size_t)V[k].n_ids * 4u;
        if (off > fsz) { fprintf(stderr, "NG! 途中で切れている (%u)\n", k); return 1; }
        if (V[k].n_ids > max_ids) max_ids = V[k].n_ids;
        if (V[k].text_len > max_text) max_text = V[k].text_len;
    }

    printf("ベクタ %s: %u 件 / 最長 %u ids / 最長入力 %u B\n\n",
           argv[1], n_vec, max_ids, max_text);

    int bad = 0;

    bad += gate0();
    printf("\n");

    /* --- G1 テーブルのハッシュ ------------------------------------------- */
    printf("G1 テーブルの SHA-256（ベクタと実装が同じテーブルから来ているか）\n");
    if (memcmp(file_sha, saan_g2p_table_sha256, 32) != 0) {
        printf("  NG! ベクタ側 ");
        for (int i = 0; i < 8; ++i) printf("%02x", file_sha[i]);
        printf("... / 実装側 ");
        for (int i = 0; i < 8; ++i) printf("%02x", saan_g2p_table_sha256[i]);
        printf("...\n");
        printf("      ⚠️ 違うテーブルで作ったベクタと突き合わせている。"
               "gen_g2p_tables.py を走らせ直すこと\n");
        ++bad;
    } else {
        printf("  OK  一致\n");
    }
    if (saan_g2p_table_entries != 195) {
        printf("  NG! テーブル件数 %d（195 のはず）\n", (int)saan_g2p_table_entries);
        ++bad;
    } else {
        printf("  OK  mora テーブル 195 エントリ\n");
    }
    printf("\n");

    /* --- G2 件数の下限 ---------------------------------------------------- */
    printf("G2 ベクタ件数の下限（空ファイルで満点を取らせない）\n");
    uint32_t n_ok_vec = 0, n_err_vec = 0;
    for (uint32_t k = 0; k < n_vec; ++k) {
        if (V[k].kind == KIND_OK) ++n_ok_vec; else ++n_err_vec;
    }
    if ((long)n_vec < min_vectors) {
        printf("  NG! %u 件（下限 %ld）\n", n_vec, min_vectors);
        ++bad;
    } else {
        printf("  OK  %u 件 >= %ld\n", n_vec, min_vectors);
    }
    /* ⚠️ allow 側と deny 側の**両方**を持っていること。片方だけだと
     *    「全部 OK を返す」「全部エラーを返す」実装で満点が取れる */
    if (n_ok_vec == 0 || n_err_vec == 0) {
        printf("  NG! 正常 %u / エラー期待 %u — 片側しか無いテストは空虚\n",
               n_ok_vec, n_err_vec);
        ++bad;
    } else {
        printf("  OK  正常 %u 件 / エラー期待 %u 件（両側ある）\n", n_ok_vec, n_err_vec);
    }
    printf("\n");

    /* --- 出力バッファを 1 つ確保して使い回す ------------------------------ */
    const int32_t cap = (int32_t)max_ids + 8;
    obuf_t O;
    O.cap = cap;
    O.buf = (int32_t *)malloc(sizeof(int32_t) * (size_t)(cap + GUARD_N));

    /* --- G3 パリティ ------------------------------------------------------ */
    printf("G3 パリティ（Python と ids が完全一致するか。相関ではなく整数の一致）\n");
    uint32_t n_match = 0, n_mismatch = 0;
    int shown = 0;
    for (uint32_t k = 0; k < n_vec; ++k) {
        if (V[k].kind != KIND_OK) continue;
        obuf_poison(&O);
        int32_t n = -1;
        saan_g2p_info info;
        memset(&info, 0x5a, sizeof info);
        saan_g2p_status s = saan_g2p((const char *)V[k].text, V[k].text_len,
                                     O.buf, cap, &n, &info);
        int ok = (s == SAAN_G2P_OK) && (n == (int32_t)V[k].n_ids);
        int32_t first = -1;
        if (ok) {
            for (int32_t i = 0; i < n; ++i)
                if (O.buf[i] != V[k].ids[i]) { first = i; ok = 0; break; }
        }
        /* 黙って落ちた件数と音素数も突き合わせる。**ids だけ見ていると
         * 「`ー` を落とす／落とさない」の違いが別の場所で相殺されうる** */
        if (ok && (info.n_phonemes != (int32_t)V[k].n_phonemes
                || info.n_pad_phonemes != (int32_t)V[k].n_pad
                || info.n_dropped_long != (int32_t)V[k].n_drop_long
                || info.n_dropped_devoice != (int32_t)V[k].n_drop_devoice)) ok = 0;
        if (ok && !obuf_guard_ok(&O, n)) ok = 0;

        if (ok) { ++n_match; continue; }
        ++n_mismatch;
        if (!shown) {                      /* **最初の 1 件は必ず全部出す** */
            shown = 1;
            printf("  NG! 最初の不一致: %s\n", vname(&V[k]));
            dump_text(V[k].text, V[k].text_len);
            printf("      status %d (%s) / n_ids 期待 %u 実際 %d\n",
                   (int)s, saan_g2p_strerror(s), V[k].n_ids, n);
            if (first >= 0) {
                printf("      最初にずれた位置 i=%d: 期待 %d / 実際 %d\n",
                       first, V[k].ids[first], O.buf[first]);
                dump_ids("期待", V[k].ids, (int32_t)V[k].n_ids, first);
                dump_ids("実際", O.buf, n > 0 ? n : 0, first);
            }
            printf("      音素数 期待 %u/実際 %d, PAD 期待 %u/実際 %d, "
                   "ー drop 期待 %u/実際 %d, ° drop 期待 %u/実際 %d\n",
                   V[k].n_phonemes, info.n_phonemes, V[k].n_pad, info.n_pad_phonemes,
                   V[k].n_drop_long, info.n_dropped_long,
                   V[k].n_drop_devoice, info.n_dropped_devoice);
        }
    }
    printf("  %s 一致 %u / %u\n", n_mismatch ? "NG!" : "OK ", n_match, n_ok_vec);
    bad += n_mismatch != 0;
    printf("\n");

    /* --- G4 長さの不変量 -------------------------------------------------- */
    printf("G4 PAD 規則の不変量 n_ids == 2*p + 3 - k == 2*(p-k) + 3 + k\n");
    uint32_t n_bad_len = 0;
    for (uint32_t k = 0; k < n_vec; ++k) {
        if (V[k].kind != KIND_OK) continue;
        obuf_poison(&O);
        int32_t n = -1;
        saan_g2p_info info;
        memset(&info, 0x5a, sizeof info);
        if (saan_g2p((const char *)V[k].text, V[k].text_len, O.buf, cap, &n, &info)
            != SAAN_G2P_OK) { ++n_bad_len; continue; }
        const int32_t p = info.n_phonemes, q = info.n_pad_phonemes;
        /* ⚠️ 単位が 2 通りある。**両方**書いて両方 assert する（C-019 と同型の罠） */
        if (n != 2 * p + 3 - q || n != 2 * (p - q) + 3 + q) {
            if (n_bad_len == 0)
                printf("  NG! %s: n_ids=%d p=%d k=%d → 2p+3-k=%d / 2(p-k)+3+k=%d\n",
                       vname(&V[k]), n, p, q, 2 * p + 3 - q, 2 * (p - q) + 3 + q);
            ++n_bad_len;
        }
    }
    printf("  %s %u / %u 件で成立\n", n_bad_len ? "NG!" : "OK ",
           n_ok_vec - n_bad_len, n_ok_vec);
    bad += n_bad_len != 0;
    printf("\n");

    /* --- G5 エラー経路 ---------------------------------------------------- */
    printf("G5 エラー経路（未知文字 / 不正 UTF-8。err_byte まで一致するか）\n");
    uint32_t n_err_ok = 0, n_err_bad = 0;
    int shown_err = 0;
    for (uint32_t k = 0; k < n_vec; ++k) {
        if (V[k].kind == KIND_OK) continue;
        const saan_g2p_status want = (V[k].kind == KIND_ERR_UTF8)
                                   ? SAAN_G2P_ERR_UTF8 : SAAN_G2P_ERR_UNKNOWN;
        obuf_poison(&O);
        int32_t n = -12345;
        saan_g2p_info info;
        memset(&info, 0x5a, sizeof info);
        saan_g2p_status s = saan_g2p((const char *)V[k].text, V[k].text_len,
                                     O.buf, cap, &n, &info);
        /* 失敗しても**バッファの外を触らない**こと（毒がそのまま残ること） */
        const int clean = obuf_guard_ok(&O, 0);
        const int ok = (s == want) && (info.err_byte == V[k].err_byte) && clean;
        if (ok) { ++n_err_ok; continue; }
        ++n_err_bad;
        if (!shown_err) {
            shown_err = 1;
            printf("  NG! 最初の不一致: %s\n", vname(&V[k]));
            dump_text(V[k].text, V[k].text_len);
            printf("      status 期待 %d (%s) / 実際 %d (%s)\n",
                   (int)want, saan_g2p_strerror(want), (int)s, saan_g2p_strerror(s));
            printf("      err_byte 期待 %d / 実際 %d%s\n",
                   V[k].err_byte, info.err_byte,
                   clean ? "" : "   ⚠️ 失敗したのに ids に書き込んだ");
        }
    }
    printf("  %s %u / %u 件\n", n_err_bad ? "NG!" : "OK ", n_err_ok, n_err_vec);
    bad += n_err_bad != 0;
    printf("\n");

    /* --- G6 バッファ不足 -------------------------------------------------- */
    printf("G6 バッファ不足（ERR_OVERFLOW を返し、cap を 1 個も超えて書かない）\n");
    uint32_t n_of_ok = 0, n_of_bad = 0, n_of_tried = 0;
    int shown_of = 0;
    for (uint32_t k = 0; k < n_vec; ++k) {
        if (V[k].kind != KIND_OK || V[k].n_ids == 0) continue;
        /* 全件やると遅いので 7 件に 1 件 + 最初の 32 件。最小 cap=0 も 1 回混ぜる */
        if (k > 32 && (k % 7u) != 0u) continue;
        ++n_of_tried;
        const int32_t tight = (int32_t)V[k].n_ids - 1;
        obuf_poison(&O);
        int32_t n = -12345;
        saan_g2p_info info;
        memset(&info, 0x5a, sizeof info);
        saan_g2p_status s = saan_g2p((const char *)V[k].text, V[k].text_len,
                                     O.buf, tight, &n, &info);
        const int clean = obuf_guard_ok(&O, tight);   /* tight より後ろは無傷か */
        if (s == SAAN_G2P_ERR_OVERFLOW && clean) { ++n_of_ok; continue; }
        ++n_of_bad;
        if (!shown_of) {
            shown_of = 1;
            printf("  NG! 最初の不一致: %s (n_ids=%u, cap=%d)\n",
                   vname(&V[k]), V[k].n_ids, tight);
            printf("      status 期待 %d (ERR_OVERFLOW) / 実際 %d (%s)%s\n",
                   (int)SAAN_G2P_ERR_OVERFLOW, (int)s, saan_g2p_strerror(s),
                   clean ? "" : "   ⚠️ cap を超えて書き込んだ");
        }
    }
    /* cap=0 は必ず 1 回試す（境界の下端） */
    {
        obuf_poison(&O);
        int32_t n = -12345;
        saan_g2p_info info;
        saan_g2p_status s = saan_g2p(kG0Text, sizeof kG0Text - 1, O.buf, 0, &n, &info);
        ++n_of_tried;
        if (s == SAAN_G2P_ERR_OVERFLOW && obuf_guard_ok(&O, 0)) ++n_of_ok;
        else {
            ++n_of_bad;
            printf("  NG! cap=0 で ERR_OVERFLOW にならない: %d (%s)\n",
                   (int)s, saan_g2p_strerror(s));
        }
    }
    printf("  %s %u / %u 件\n", n_of_bad ? "NG!" : "OK ", n_of_ok, n_of_tried);
    bad += n_of_bad != 0;
    printf("\n");

    /* --- G7 陰性対照（交差比較）------------------------------------------ */
    printf("G7 陰性対照: 別ベクタの期待 ids を当てると必ず不一致になるか\n");
    uint32_t n_cross = 0, n_cross_bad = 0;
    for (uint32_t k = 0; k + 1 < n_vec; ++k) {
        if (V[k].kind != KIND_OK) continue;
        /* 期待 ids が本当に別物なベクタを 1 つ探す */
        uint32_t j = k + 1;
        while (j < n_vec && (V[j].kind != KIND_OK
               || (V[j].n_ids == V[k].n_ids
                   && memcmp(V[j].ids, V[k].ids, (size_t)V[k].n_ids * 4u) == 0))) ++j;
        if (j >= n_vec) continue;
        if ((k % 137u) != 0u) continue;         /* 全対は要らない。散らして 20 件程度 */
        obuf_poison(&O);
        int32_t n = -1;
        if (saan_g2p((const char *)V[k].text, V[k].text_len, O.buf, cap, &n, NULL)
            != SAAN_G2P_OK) continue;           /* G3 が既に落としている */
        ++n_cross;
        if (n == (int32_t)V[j].n_ids
            && memcmp(O.buf, V[j].ids, (size_t)n * 4u) == 0) {
            printf("  NG! %s の出力が別ベクタ %s の期待と一致した — 比較が効いていない\n",
                   vname(&V[k]), vname(&V[j]));
            ++n_cross_bad;
        }
    }
    if (n_cross == 0) {
        printf("  --  交差比較を 1 件も実行できなかった（G3 が落ちている間は無意味）\n");
    } else {
        printf("  %s %u 組すべて不一致\n", n_cross_bad ? "NG!" : "OK ", n_cross);
        bad += n_cross_bad != 0;
    }
    printf("\n");

    /* --- G8 黙って落ちた件数の合計 ---------------------------------------- */
    printf("G8 黙って落ちた件数（`ー` と `°`）の合計が Python と一致するか\n");
    {
        long want_long = 0, want_dev = 0, got_long = 0, got_dev = 0;
        for (uint32_t k = 0; k < n_vec; ++k) {
            if (V[k].kind != KIND_OK) continue;
            want_long += V[k].n_drop_long;
            want_dev  += V[k].n_drop_devoice;
            int32_t n = -1;
            saan_g2p_info info;
            memset(&info, 0, sizeof info);
            if (saan_g2p((const char *)V[k].text, V[k].text_len, O.buf, cap, &n, &info)
                == SAAN_G2P_OK) {
                got_long += info.n_dropped_long;
                got_dev  += info.n_dropped_devoice;
            }
        }
        const int ok = (want_long == got_long) && (want_dev == got_dev);
        printf("  %s ー drop 期待 %ld / 実際 %ld、° drop 期待 %ld / 実際 %ld\n",
               ok ? "OK " : "NG!", want_long, got_long, want_dev, got_dev);
        if (want_long == 0 && want_dev == 0)
            printf("      ⚠️ 期待が 0/0 のベクタ集合ではこのゲートは空虚\n");
        bad += !ok;
    }

    printf("\n%s\n", bad ? "NG: 落ちたゲートがある" : "OK: 全ゲート通過");
    free(O.buf); free(V); free(fb);
    return bad ? 1 : 0;
}
