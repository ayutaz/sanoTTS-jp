/* esp32/main の .c をホストで動かし、出力を csrc/golden.bin の out.pcm と突き合わせる。
 *
 * **これで潰せるのはアプリ側のロジックだけ**:
 *   フレーム数とサンプル数の取り違え / 端数チャンクの落とし / プリロールの順序 /
 *   int16 変換 / arena サイズ / 下限ガード。
 *
 * **潰せないもの**: idf.py build、実 SRAM、実レイテンシ、I2S の実サンプルレート、
 *   FreeRTOS のスケジューリング、flash mmap と D-cache の相互作用。
 *
 *   ./saan_hoststub ../csrc/student.bin ../csrc/golden.bin [out.wav]
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "driver/i2s_std.h"
#include "esp_partition.h"

#include "saanotts.h"

#include "demo_ids.h"

#define SAAN_HOSTSTUB_N_IDS SAAN_DEMO_N_IDS

void app_main(void);

static void *slurp(const char *path, size_t *size) {
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "開けない: %s\n", path); exit(1); }
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    /* ⚠️ 64 KB 境界に置く。実機の esp_partition_mmap は 64 KB 境界に丸めた
     * ページを返すので、それより弱い条件でテストすると
     * main.c のアライン assert が本番だけ意味を持つことになってしまう */
    void *raw = malloc((size_t)n + 65536);
    if (!raw) exit(1);
    uintptr_t aligned = ((uintptr_t)raw + 65535u) & ~(uintptr_t)65535u;
    void *b = (void *)aligned;
    if (fread(b, 1, (size_t)n, f) != (size_t)n) { fprintf(stderr, "読めない\n"); exit(1); }
    fclose(f); *size = (size_t)n; return b;
}

static int16_t f2i(float x) {
    long v = lrintf(x * 32767.0f);
    if (v > 32767) v = 32767;
    if (v < -32768) v = -32768;
    return (int16_t)v;
}

#define HDR_ENT (64 + 4 + 4 + 16 + 8 + 8)

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s student.bin golden.bin [out.wav]\n", argv[0]);
        return 2;
    }
    size_t wsz, gsz;
    void *wbuf = slurp(argv[1], &wsz), *gbuf = slurp(argv[2], &gsz);
    saan_stub_set_blob(wbuf, (uint32_t)wsz);

    app_main();

    size_t got_n = 0;
    const int16_t *got = saan_stub_i2s_pcm(&got_n);

    /* golden の out.pcm を引く */
    saan_weights G;
    if (saan_weights_open(&G, gbuf, gsz) != SAAN_OK) {
        fprintf(stderr, "golden が読めない\n"); return 1;
    }
    uint64_t nb = 0;
    const float *ref = (const float *)saan_tensor(&G, "out.pcm", NULL, NULL, &nb);
    if (!ref) { fprintf(stderr, "golden に out.pcm が無い\n"); return 1; }
    size_t ref_n = (size_t)(nb / sizeof(float));

    printf("\n== ホスト stub の突き合わせ ==\n");
    printf("  I2S に書いたサンプル数 : %zu\n", got_n);
    printf("  golden out.pcm         : %zu\n", ref_n);

    printf("  ⚠️ ヒープ / スタックの数値は stub が 0 を返しているだけ。"
           "**実機の値ではない**\n");

    int bad = 0;
    if (got_n != ref_n) {
        printf("  NG! サンプル数が違う（端数チャンクを落としたか、"
               "フレーム数をサンプル数と取り違えている）\n");
        ++bad;
    }

    /* --- 判定 1（**厳密**）: C コア自身の一括版と bit 完全一致 -------------
     *
     * これがアプリ側の責任範囲そのもの: フレーム数とサンプル数の取り違え、
     * 端数チャンクの落とし、プリロールの順序、int16 変換。
     * コアがストリーミングと一括で bit 一致することは既に
     * `make -C csrc stream` の G2 が保証しているので、ここは
     * **一括版 → int16 と突き合わせれば足りる**。 */
    {
        saan_weights W;
        if (saan_weights_open(&W, wbuf, wsz) != SAAN_OK) {
            printf("  NG! 重みが読めない\n"); ++bad;
        } else {
            size_t need = saan_arena_needed(SAAN_HOSTSTUB_N_IDS);
            void *ab = malloc(need);
            saan_arena A; saan_arena_init(&A, ab, need);
            saan_output out;
            saan_status s = saan_synthesize(&W, &A, kSaanDemoIds,
                                            SAAN_HOSTSTUB_N_IDS, SAAN_S_V, &out);
            if (s != SAAN_OK) {
                printf("  NG! 一括版が走らない: %s\n", saan_strerror(s)); ++bad;
            } else if ((size_t)out.n_samples != got_n) {
                printf("  NG! 一括版 %d sample vs アプリ %zu sample\n",
                       out.n_samples, got_n); ++bad;
            } else {
                size_t nd = 0; size_t first = 0; int maxd = 0;
                for (size_t i = 0; i < got_n; ++i) {
                    int16_t e = f2i(out.pcm[i]);
                    if (got[i] != e) {
                        if (!nd) first = i;
                        ++nd;
                        int d = got[i] - e; if (d < 0) d = -d;
                        if (d > maxd) maxd = d;
                    }
                }
                if (nd) {
                    printf("  NG! [厳密] C 一括版と %zu/%zu sample 違う"
                           "（最初 %zu, max|Δ| %d）\n", nd, got_n, first, maxd);
                    ++bad;
                } else {
                    printf("  OK  [厳密] C 一括版 → int16 と %zu sample "
                           "**bit 完全一致**\n", got_n);
                }
            }
            free(ab);
        }
    }

    /* --- 判定 2: Python 参照実装（golden）との差 ---------------------------
     *
     * ⚠️ **判定基準は blob の dtype で変わる。**
     *
     * fp32 blob: golden.bin は Python 参照実装の出力で、C コアとは
     *   SNR 117.50 dB / max|Δ| 5.8e-07 で一致する（`make -C csrc test` の判定は
     *   Pearson >= 0.98）。float で 6e-07 ずれると丸め境界のサンプルだけ
     *   int16 で 1 LSB 飛ぶ。**max|Δ| が 1 を超えたら異常**。
     *
     * int8 blob: 量子化そのもので波形が変わる。LSB 一致は**要求できない**。
     *   M-39 の PTQ 実測と同じ水準（波形 SNR >= 25 dB）で判定する。 */
    if (got_n == ref_n) {
        int is_int8 = 0;
        {
            saan_weights W2;
            if (saan_weights_open(&W2, wbuf, wsz) == SAAN_OK) {
                uint32_t dt = 0;
                if (saan_tensor(&W2, "decoder.hout.weight", &dt, NULL, NULL))
                    is_int8 = (dt != 0u);
            }
        }
        size_t ndiff = 0; int maxd = 0; size_t first = 0;
        double se = 0.0, sp = 0.0;
        for (size_t i = 0; i < ref_n; ++i) {
            int16_t e = f2i(ref[i]);
            double d = (double)got[i] - (double)e;
            se += d * d; sp += (double)e * (double)e;
            if (got[i] != e) {
                if (!ndiff) first = i;
                ++ndiff;
                int a2 = got[i] - e; if (a2 < 0) a2 = -a2;
                if (a2 > maxd) maxd = a2;
            }
        }
        double snr = (se > 0.0 && sp > 0.0) ? 10.0 * log10(sp / se) : 999.0;
        printf("  blob の dtype: %s\n", is_int8 ? "int8" : "fp32");
        printf("  [参考] Python 参照(golden) との差: %zu/%zu sample, "
               "max|Δ| %d LSB, SNR %.2f dB", ndiff, ref_n, maxd, snr);
        if (ndiff) printf("（最初 %zu）", first);
        printf("\n");
        if (is_int8) {
            printf("  %s int8 は量子化で波形が変わる。判定は SNR >= 25 dB"
                   "（M-39 の PTQ 実測と同水準）\n", snr >= 25.0 ? "OK " : "NG!");
            if (snr < 25.0) ++bad;
        } else {
            printf("  %s fp32 は Python 参照と max|Δ| <= 1 LSB が正常"
                   "（コアは SNR 117.50 dB で一致）\n", maxd <= 1 ? "OK " : "NG!");
            if (maxd > 1) ++bad;
        }
    }

    if (argc > 3 && got_n) {
        FILE *f = fopen(argv[3], "wb");
        if (f) {
            uint32_t sr = SAAN_SR, data = (uint32_t)(got_n * 2), riff = 36 + data;
            uint16_t one = 1, ch = 1, bps = 16, ba = 2;
            uint32_t fmtsz = 16, byterate = sr * 2;
            fwrite("RIFF", 1, 4, f); fwrite(&riff, 4, 1, f); fwrite("WAVE", 1, 4, f);
            fwrite("fmt ", 1, 4, f); fwrite(&fmtsz, 4, 1, f); fwrite(&one, 2, 1, f);
            fwrite(&ch, 2, 1, f); fwrite(&sr, 4, 1, f); fwrite(&byterate, 4, 1, f);
            fwrite(&ba, 2, 1, f); fwrite(&bps, 2, 1, f);
            fwrite("data", 1, 4, f); fwrite(&data, 4, 1, f);
            fwrite(got, 2, got_n, f);
            fclose(f);
            printf("  WAV: %s (%zu sample / %.3f s)\n", argv[3], got_n,
                   (double)got_n / SAAN_SR);
        }
    }
    printf("\n%s\n", bad ? "NG: ホスト stub の突き合わせに失敗"
                         : "ホスト stub: アプリ側ロジックは C コアと bit 一致");
    return bad ? 1 : 0;
}
