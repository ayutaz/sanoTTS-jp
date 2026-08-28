/* held-out の音素ID列を合成して WAV に書き出す（P-1 の知覚評価用）
 *
 * **なぜ要るか**: int8 経路はこれまで **SNR でしか検証していない**。
 * 「fp32 比 25.88 dB」が可聴かどうかは一度も測っていないし、PIE が必要とする
 * W8A8（23.24 dB）が使えるかも SNR では答えられない。SCOREQ で測るには
 * 波形ファイルが要る。
 *
 * ⚠️ **`d̂` は fp32 側に固定する。** 固定しないと経路ごとにフレーム数が変わり、
 * 同じ発話の比較にならない（`int8_e2e_test.c` と同じ理由）。
 * ⚠️ 固定するので、これは「duration を除いた音響経路の差」を測る道具である。
 * `d̂` 自体の差（W8A32 で 98.8%、W8A8 で 97.6% 一致）は別に評価すること。
 *
 * W8A32 と W8A8 の切り替えは **コンパイル時** の `-DSAAN_INT8_ACT`:
 *
 *   cc -std=c99 -O2 -o dump_pcm dump_pcm.c \
 *      saanotts.c saanotts_stream.c saanotts_int8.c fft.c -lm
 *   ./dump_pcm student.bin      ids_heldout.bin out/fp32
 *   ./dump_pcm student_i8.bin   ids_heldout.bin out/w8a32   # 既定
 *   cc ... -DSAAN_INT8_ACT=1 ... && ./dump_pcm student_i8.bin ids_heldout.bin out/w8a8
 *
 * ⚠️ **`--ref` を渡したときだけ `d̂` を固定できる。** 省くとその経路自身の `d̂` で
 * 走るので、レーン間でサンプル数が揃わない。SCOREQ は長さが違っても動くが、
 * **同じ発話の比較にならない**ので既定では必ず `--ref` を使うこと。
 */
#include "saanotts.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

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

/* ⚠️ `int8_e2e_test.c:74` と**同じ実装**にすること。切り捨てと丸めを取り違えると
 * 同じ ids ブロブから別の音素列が出て、レーン比較が黙って壊れる */
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

static void wr16(FILE *f, unsigned v) { fputc(v & 0xff, f); fputc((v >> 8) & 0xff, f); }
static void wr32(FILE *f, unsigned v) { wr16(f, v & 0xffff); wr16(f, v >> 16); }

/* 22.05 kHz / mono / 16-bit PCM の WAV。⚠️ 丸めは int8_e2e_test と同じ
 * half-away-from-zero にせず、**評価スクリプト側と同じ** lrintf を使う */
static void write_wav(const char *path, const float *pcm, int n) {
    FILE *f = fopen(path, "wb");
    if (!f) { fprintf(stderr, "書けない: %s\n", path); exit(1); }
    const unsigned data = (unsigned)n * 2u;
    fwrite("RIFF", 1, 4, f); wr32(f, 36u + data); fwrite("WAVE", 1, 4, f);
    fwrite("fmt ", 1, 4, f); wr32(f, 16); wr16(f, 1); wr16(f, 1);
    wr32(f, 22050); wr32(f, 22050u * 2u); wr16(f, 2); wr16(f, 16);
    fwrite("data", 1, 4, f); wr32(f, data);
    for (int i = 0; i < n; ++i) {
        float v = pcm[i];
        if (v > 1.0f) v = 1.0f;
        if (v < -1.0f) v = -1.0f;
        long q = (long)(v * 32767.0f + (v >= 0.0f ? 0.5f : -0.5f));
        if (q > 32767) q = 32767;
        if (q < -32768) q = -32768;
        wr16(f, (unsigned)(q & 0xffff));
    }
    fclose(f);
}

int main(int argc, char **argv) {
    const char *ref_path = NULL;
    int argi = 1;
    if (argc > 2 && strcmp(argv[1], "--ref") == 0) { ref_path = argv[2]; argi = 3; }
    if (argc - argi < 3) {
        fprintf(stderr,
                "usage: %s [--ref fp32blob] blob.bin ids.bin outdir\n"
                "  --ref を渡すと d̂ をその重みの出力に固定する（レーン比較には必須）\n",
                argv[0]);
        return 2;
    }
    const char *blob_path = argv[argi], *ids_path = argv[argi + 1];
    const char *outdir = argv[argi + 2];

    size_t bsz = 0, hsz = 0, rsz = 0;
    void *bb = slurp(blob_path, &bsz), *hb = slurp(ids_path, &hsz);
    saan_weights W, H, R;
    if (saan_weights_open(&W, bb, bsz) != SAAN_OK) { fprintf(stderr, "blob 不正\n"); return 1; }
    if (saan_weights_open(&H, hb, hsz) != SAAN_OK) { fprintf(stderr, "ids 不正\n"); return 1; }
    void *rb = NULL;
    if (ref_path) {
        rb = slurp(ref_path, &rsz);
        if (saan_weights_open(&R, rb, rsz) != SAAN_OK) { fprintf(stderr, "ref 不正\n"); return 1; }
    }

    printf("blob=%s  ids=%s  out=%s  d̂固定=%s  ACT=%s\n",
           blob_path, ids_path, outdir, ref_path ? ref_path : "しない",
#if SAAN_INT8_ACT
           "W8A8"
#else
           "W8A32(既定)"
#endif
    );

    int32_t ids[4096];
    int n_ok = 0;
    long total_samples = 0;
    for (int k = 0; k < 4096; ++k) {
        char nm[32], path[512];
        snprintf(nm, sizeof nm, "ids.%03d", k);
        const int n = read_ids(&H, nm, ids, 4096);
        if (n <= 0) break;

        const size_t need = saan_arena_needed(n);
        void *am = malloc(need);
        if (!am) { fprintf(stderr, "arena 確保失敗\n"); return 1; }
        saan_arena A;
        saan_arena_init(&A, am, need);
        saan_output o;
        saan_status s;
        if (ref_path) {
            /* 参照側の d̂ を先に出す */
            void *ar = malloc(need);
            if (!ar) { fprintf(stderr, "arena 確保失敗\n"); return 1; }
            saan_arena AR;
            saan_arena_init(&AR, ar, need);
            saan_output oref;
            s = saan_synthesize(&R, &AR, ids, n, SAAN_S_V, &oref);
            if (s != SAAN_OK) { fprintf(stderr, "ref: %s\n", saan_strerror(s)); return 1; }
            s = saan_synthesize_d(&W, &A, ids, n, SAAN_S_V, oref.d_hat, &o);
            free(ar);
        } else {
            s = saan_synthesize(&W, &A, ids, n, SAAN_S_V, &o);
        }
        if (s != SAAN_OK) { fprintf(stderr, "synth %03d: %s\n", k, saan_strerror(s)); return 1; }

        snprintf(path, sizeof path, "%s/u%03d.wav", outdir, k);
        write_wav(path, o.pcm, o.n_samples);
        total_samples += o.n_samples;
        ++n_ok;
        free(am);
    }
    if (n_ok == 0) { fprintf(stderr, "ids ブロブが空\n"); return 1; }
    printf("  %d 文 / 計 %.2f 秒を書き出した\n", n_ok, (double)total_samples / 22050.0);
    return 0;
}
