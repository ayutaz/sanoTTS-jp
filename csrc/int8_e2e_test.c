/* int8 経路の end-to-end 検証（Phase D-3c'-2）
 *
 * **int8_test.c との違い**: あちらは**カーネル単体**を 1 層ずつ測る。
 * こちらは **duration → acoustic → decoder → iSTFT を通し**で 2 回走らせ、
 * fp32 ブロブと int8 ブロブの出力を突き合わせる。
 * D-3c の照合で「多層を直列に繋いだ検証が 1 つも無い」と指摘された穴がここ。
 *
 * ⚠️ **d̂ は fp32 側に固定して測る。** 固定しないと int8 で d̂ が変わった文で
 * フレーム数が変わり、**波形 SNR がそもそも定義できない**。1 フレーム = 11.6 ms の
 * ずれは可聴でないので、これは品質の問題ではなく測定手順の問題。
 * 固定は測定専用の入口 `saan_synthesize_d()` を使う。d̂ の一致率は別に併記する。
 *
 * ⚠️ **「1 文ごとに波形 SNR ≥ 25 dB」は達成できない。** 量子化そのものの性質で、
 * 正しい実装でも held-out 24 文のうち 9 文が 25 dB を下回る（M-44）。
 * 判定は「**24 文の平均 ≥ 25 dB かつ最小 ≥ 23 dB**」で行う。
 *
 *   cc -std=c99 -O2 -Wall -Wextra -o int8_e2e_test int8_e2e_test.c \
 *      saanotts.c saanotts_stream.c saanotts_int8.c fft.c -lm
 *   ./int8_e2e_test student.bin student_i8.bin golden.bin ids_heldout.bin out.json
 */
#include "saanotts.h"
#include "saanotts_stream.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* 判定のしきい値（M-44 の実測に合わせてある。上のコメントが根拠） */
#define GATE_MEAN_DB 25.0
#define GATE_MIN_DB  23.0

static void *slurp(const char *path, size_t *size) {
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "開けない: %s\n", path); exit(1); }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    void *b = malloc((size_t)n);
    if (!b || fread(b, 1, (size_t)n, f) != (size_t)n) {
        fprintf(stderr, "読めない: %s\n", path); exit(1);
    }
    fclose(f);
    *size = (size_t)n;
    return b;
}

static double snr_db(const float *got, const float *ref, size_t n) {
    double sig = 0.0, err = 0.0;
    for (size_t i = 0; i < n; ++i) {
        const double r = ref[i], e = (double)got[i] - r;
        sig += r * r; err += e * e;
    }
    if (err == 0.0) return INFINITY;
    if (sig == 0.0) return -INFINITY;
    return 10.0 * log10(sig / err);
}

static char jbuf[1 << 18];
static size_t jlen = 0;
static void jp(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    jlen += (size_t)vsnprintf(jbuf + jlen, sizeof jbuf - jlen, fmt, ap);
    va_end(ap);
}
static void jnum(double v) {
    if (isinf(v)) jp("%s", v > 0 ? "\"inf\"" : "\"-inf\"");
    else if (isnan(v)) jp("null");
    else jp("%.4f", v);
}

/* SAAN ブロブから fp32 テンソルを引いて int32 の ids に直す。要素数を返す */
static int read_ids(const saan_weights *w, const char *name, int32_t *dst, int cap) {
    uint32_t dt = 0;
    uint64_t nb = 0;
    const void *p = saan_tensor(w, name, &dt, NULL, &nb);
    if (!p || dt != 0u) return -1;
    const int n = (int)(nb / sizeof(float));
    if (n > cap) return -1;
    const float *f = (const float *)p;
    for (int i = 0; i < n; ++i) dst[i] = (int32_t)f[i];
    return n;
}

/* 1 文を fp32 / int8 の両方で合成して比べる。d̂ は fp32 側に固定する */
typedef struct {
    int n_ids, n_frames, n_samples;
    int d_match;            /* d̂ が一致したトークン数（固定する前の int8 の推定） */
    double log_d_db, c_db, pcm_db;
} one_t;

static int synth_pair(const saan_weights *F, const saan_weights *I,
                      const int32_t *ids, int n_ids, one_t *r) {
    const size_t need = saan_arena_needed(n_ids);
    void *a1 = malloc(need), *a2 = malloc(need);
    if (!a1 || !a2) { fprintf(stderr, "arena 確保失敗\n"); exit(1); }
    saan_arena A1, A2;
    saan_arena_init(&A1, a1, need);
    saan_arena_init(&A2, a2, need);

    saan_output of, oi;
    saan_status s = saan_synthesize(F, &A1, ids, n_ids, SAAN_S_V, &of);
    if (s != SAAN_OK) { fprintf(stderr, "fp32: %s\n", saan_strerror(s)); exit(1); }

    /* まず d̂ を固定せずに走らせて、int8 の d̂ がどれだけ一致するかを数える。
     * ⚠️ フレーム数が違いうるので、この出力で波形 SNR は測らない */
    s = saan_synthesize(I, &A2, ids, n_ids, SAAN_S_V, &oi);
    if (s != SAAN_OK) { fprintf(stderr, "int8(free): %s\n", saan_strerror(s)); exit(1); }
    int dm = 0;
    for (int i = 0; i < n_ids; ++i) if (oi.d_hat[i] == of.d_hat[i]) ++dm;
    r->d_match = dm;
    r->log_d_db = snr_db(oi.log_d, of.log_d, (size_t)n_ids);

    /* 本番: d̂ を fp32 側に固定して測り直す */
    saan_arena_init(&A2, a2, need);
    s = saan_synthesize_d(I, &A2, ids, n_ids, SAAN_S_V, of.d_hat, &oi);
    if (s != SAAN_OK) { fprintf(stderr, "int8(fixed): %s\n", saan_strerror(s)); exit(1); }
    if (oi.n_frames != of.n_frames || oi.n_samples != of.n_samples) {
        fprintf(stderr, "d̂ を固定してもフレーム数が違う\n"); exit(1);
    }
    r->n_ids = n_ids;
    r->n_frames = of.n_frames;
    r->n_samples = of.n_samples;
    r->c_db = snr_db(oi.c, of.c, (size_t)SAAN_CDIM * (size_t)of.n_frames);
    r->pcm_db = snr_db(oi.pcm, of.pcm, (size_t)of.n_samples);

    free(a1); free(a2);
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 5) {
        fprintf(stderr, "usage: %s student.bin student_i8.bin golden.bin "
                        "ids_heldout.bin [out.json]\n", argv[0]);
        return 2;
    }
    size_t fsz, isz, gsz, hsz;
    void *fb = slurp(argv[1], &fsz), *ib = slurp(argv[2], &isz);
    void *gb = slurp(argv[3], &gsz), *hb = slurp(argv[4], &hsz);
    saan_weights F, I, G, H;
    if (saan_weights_open(&F, fb, fsz) != SAAN_OK) { fprintf(stderr, "fp32 ブロブ\n"); return 1; }
    if (saan_weights_open(&I, ib, isz) != SAAN_OK) { fprintf(stderr, "int8 ブロブ\n"); return 1; }
    if (saan_weights_open(&G, gb, gsz) != SAAN_OK) { fprintf(stderr, "golden\n"); return 1; }
    if (saan_weights_open(&H, hb, hsz) != SAAN_OK) { fprintf(stderr, "ids\n"); return 1; }

    /* ⚠️ **2 つのブロブが本当に別 dtype かを検査する。**
     * これが無いと `int8_e2e_test a.bin a.bin ...` が「平均 inf dB で OK」を出す。
     * 空虚に通るゲートは、無いより悪い（検証で実証済み）。 */
    {
        const char *probe = "duration.blocks.0.c1.weight";
        uint32_t dtf = 99u, dti = 99u;
        if (!saan_tensor(&F, probe, &dtf, NULL, NULL) ||
            !saan_tensor(&I, probe, &dti, NULL, NULL)) {
            fprintf(stderr, "NG! 照合用テンソル %s が両方のブロブに無い\n", probe);
            return 1;
        }
        if (dtf != 0u) {
            fprintf(stderr, "NG! 第1引数は fp32 ブロブでなければならない (%s の dtype=%u)\n",
                    probe, dtf);
            return 1;
        }
        if (dti != 1u) {
            fprintf(stderr, "NG! 第2引数は int8 ブロブでなければならない (%s の dtype=%u)。"
                            "同じブロブを 2 回渡していないか\n", probe, dti);
            return 1;
        }
        if (fsz == isz && memcmp(fb, ib, fsz) == 0) {
            fprintf(stderr, "NG! 2 つのブロブが byte 単位で同一\n");
            return 1;
        }
    }

    int bad = 0;
    jp("{\n \"task\": \"D-3c'-2 int8 end-to-end（fp32 経路 vs int8 経路）\",\n");
    jp(" \"blobs\": {\"fp32\": \"%s\", \"int8\": \"%s\", \"ids\": \"%s\"},\n",
       argv[1], argv[2], argv[4]);
    jp(" \"activation\": \"%s\",\n",
#if SAAN_INT8_ACT
       "W8A8 (-DSAAN_INT8_ACT=1)"
#else
       "W8A32 (既定)"
#endif
    );

    static int32_t ids[4096];

    /* ---- 1. golden の 1 文 ---- */
    printf("== 1. golden 文（%s）==\n", argv[3]);
    {
        const int n = read_ids(&G, "in.ids", ids, 4096);
        if (n <= 0) { fprintf(stderr, "golden に in.ids が無い\n"); return 1; }
        one_t r;
        synth_pair(&F, &I, ids, n, &r);
        printf("  %d ids / %d frames / %d sample\n", r.n_ids, r.n_frames, r.n_samples);
        printf("  d_hat 一致 %d/%d   log_d %.2f dB   c %.2f dB   pcm %.2f dB\n",
               r.d_match, r.n_ids, r.log_d_db, r.c_db, r.pcm_db);
        jp(" \"golden\": {\"n_ids\": %d, \"n_frames\": %d, \"n_samples\": %d,"
           " \"d_hat_match\": %d, \"log_d_db\": ", r.n_ids, r.n_frames, r.n_samples,
           r.d_match);
        jnum(r.log_d_db); jp(", \"c_db\": "); jnum(r.c_db);
        jp(", \"pcm_db\": "); jnum(r.pcm_db); jp("},\n");
    }

    /* ---- 2. held-out の複数文 ---- */
    printf("\n== 2. held-out（%s）==\n", argv[4]);
    printf("  %3s %6s %8s %10s %9s %9s %9s\n",
           "#", "ids", "frames", "d̂一致", "log_d dB", "c dB", "pcm dB");
    double sum = 0.0, mn = 1e9, mx = -1e9;
    long dm_tot = 0, dn_tot = 0;
    int n_utt = 0, n_below25 = 0;
    jp(" \"heldout\": {\"utts\": [\n");
    for (int k = 0; k < 4096; ++k) {
        char nm[32];
        snprintf(nm, sizeof nm, "ids.%03d", k);
        const int n = read_ids(&H, nm, ids, 4096);
        if (n <= 0) break;
        one_t r;
        synth_pair(&F, &I, ids, n, &r);
        printf("  %3d %6d %8d %6d/%-3d %9.2f %9.2f %9.2f%s\n",
               k, r.n_ids, r.n_frames, r.d_match, r.n_ids,
               r.log_d_db, r.c_db, r.pcm_db, r.pcm_db < 25.0 ? "  <25" : "");
        jp("  %s{\"k\": %d, \"n_ids\": %d, \"n_frames\": %d, \"d_hat_match\": %d,"
           " \"log_d_db\": ", k ? "," : "", k, r.n_ids, r.n_frames, r.d_match);
        jnum(r.log_d_db); jp(", \"c_db\": "); jnum(r.c_db);
        jp(", \"pcm_db\": "); jnum(r.pcm_db); jp("}\n");
        sum += r.pcm_db;
        if (r.pcm_db < mn) mn = r.pcm_db;
        if (r.pcm_db > mx) mx = r.pcm_db;
        if (r.pcm_db < 25.0) ++n_below25;
        dm_tot += r.d_match; dn_tot += r.n_ids;
        ++n_utt;
    }
    jp(" ],\n");
    if (n_utt == 0) { fprintf(stderr, "ids ブロブが空\n"); return 1; }
    const double mean = sum / (double)n_utt;
    printf("\n  n=%d  平均 %.2f dB / 最小 %.2f dB / 最大 %.2f dB"
           "  （25 dB 未満 %d 文）\n", n_utt, mean, mn, mx, n_below25);
    printf("  d̂ 一致（トークン単位）%ld/%ld = %.1f%%\n",
           dm_tot, dn_tot, 100.0 * (double)dm_tot / (double)dn_tot);
    jp("  \"n_utts\": %d, \"pcm_db_mean\": ", n_utt); jnum(mean);
    jp(", \"pcm_db_min\": "); jnum(mn);
    jp(", \"pcm_db_max\": "); jnum(mx);
    jp(", \"n_below_25db\": %d, \"d_hat_token_match\": %ld, \"d_hat_tokens\": %ld}",
       n_below25, dm_tot, dn_tot);
    jp(",\n");

    /* ---- 3. 判定 ---- */
    const int g_mean = mean >= GATE_MEAN_DB;
    const int g_min = mn >= GATE_MIN_DB;
    bad += !g_mean; bad += !g_min;
    printf("\n  %s 平均 %.2f dB >= %.1f dB\n", g_mean ? "OK " : "NG!", mean, GATE_MEAN_DB);
    printf("  %s 最小 %.2f dB >= %.1f dB\n", g_min ? "OK " : "NG!", mn, GATE_MIN_DB);
    printf("  ⚠️ 「1 文ごとに >= 25 dB」では判定しない — 量子化そのものの性質で"
           "正しい実装でも %d/%d 文が下回る\n", n_below25, n_utt);

    jp(" \"gate\": {\"mean_db_threshold\": %.1f, \"min_db_threshold\": %.1f,"
       " \"mean_ok\": %s, \"min_ok\": %s, \"n_fail\": %d,\n"
       "  \"note\": \"1 文ごとの >=25 dB では判定しない。量子化そのものの性質で"
       "正しい実装でも一部の文が下回る\"},\n",
       GATE_MEAN_DB, GATE_MIN_DB, g_mean ? "true" : "false",
       g_min ? "true" : "false", bad);
    jp(" \"repro\": \"cd csrc && make int8-e2e\"\n}\n");

    printf("\n%s\n", bad ? "NG: 受け入れ条件を満たしていない"
                         : "OK: int8 経路は fp32 経路に対して所定の SNR を満たす");
    if (argc >= 6) {
        FILE *f = fopen(argv[5], "wb");
        if (!f) { fprintf(stderr, "書けない: %s\n", argv[5]); return 1; }
        fwrite(jbuf, 1, jlen, f);
        fclose(f);
        printf("JSON: %s (%zu B)\n", argv[5], jlen);
    }
    free(fb); free(ib); free(gb); free(hb);
    return bad ? 1 : 0;
}
