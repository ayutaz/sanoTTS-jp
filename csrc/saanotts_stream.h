/* ストリーミング推論（Phase D-2 / D-029）
 *
 * **目的: ESP32-S3 の SRAM (512 KB) に載せる。**
 * 一括版（`saan_synthesize`）は発話全体をバッファして 1.26 MB 使う。
 *
 * 方式: **ステート保持型**。各 conv 層が左コンテキスト (k-1) フレームを保持し、
 * チャンク間で繋ぐ。**ハローの再計算がゼロ**（再計算方式は同じメモリで計算 10 倍）。
 *
 * ⚠️ **代償はレイテンシ。** 出力は受容野ぶん遅れる:
 *   acoustic frame ±20 + decoder ±16 = **36 フレーム = 0.42 秒**
 *
 * 使い方:
 *   saan_stream_init(&st, &w, arena, &ids, n_ids, s_v);
 *   while (saan_stream_pull(&st, pcm, &n) == SAAN_OK && n > 0) { i2s_write(pcm, n); }
 *
 * ⚠️ **一括版と bit 一致することが受け入れ条件**（D-029 の G2）。
 * SNR では不可 — チャンク境界で 1 フレーム落としても 20 dB 出てしまう。
 */
#ifndef SAANOTTS_STREAM_H
#define SAANOTTS_STREAM_H

#include "saanotts.h"

/* 1 回に出すフレーム数。メモリと呼び出し回数の折衷（D-029）。
 * ⚠️ 最適化していない。実機で測ってから調整する */
#define SAAN_CHUNK 8

/* 受容野（実測 / D-029）。⚠️ **旧記述の acoustic ±10 は c1 だけ数えた誤り** */
#define SAAN_HALO_AC   20   /* (c1 k=5 + c2 k=5) × 5 段 */
#define SAAN_HALO_DEC  16   /* inp k=3 + dw k=7 × 5 段 */

typedef struct {
    const saan_weights *w;
    saan_arena *a;

    const int32_t *ids;
    int32_t n_ids;
    int32_t n_frames;      /* Σ d_hat */
    int32_t *d_hat;
    float *log_d;

    void *impl;            /* 内部状態（パイプ段と作業領域） */

    int32_t pushed;        /* acoustic に入れたフレーム数（ゼロ埋め含む） */
    int32_t emitted;       /* 呼び出し側に返したフレーム数 */

    float s_v;
    size_t peak_used;      /* arena の高水位（G1 の測定用） */
    int32_t ofill_max;     /* pull ごとの obuf 充填の最大（hop）。SAAN_OBUF_HOPS の検証用 */

    /* デバッグ: 非 NULL なら c-line を絶対時刻で書き込む（[CDIM][dbg_cap]）。
     * **段ごとに切り分けるため**。本番では NULL */
    float *dbg_c;
    int32_t dbg_cap;
} saan_stream;

/* パイプライン全体の遅延（フレーム）。**受容野の合計**（D-029）:
 * AcBlock pad=4 × 5 段 + decoder inp pad=1 + dw pad=3 × 5 段 = 36 */
#define SAAN_LATENCY (4 * 5 + 1 + 3 * 5)

/* 出力の詰め替えバッファ `obuf` の深さ（hop 数）= **pull ごとの ofill の最大値**。
 *
 *   SAAN_OBUF_HOPS = 2·CH − (SAAN_LATENCY mod CH)      （CH=8: 16 − 4 = 12 = CH+4）
 *
 * ofill は定常では 1 step で CH 増えて pull で CH 減るが、末尾の step は
 * 「fpush が n_frames に届いた瞬間に残りを全部吐く」（istft_ready）ので、
 * 発話の長さ次第で 1 step に CH を超える hop が積み上がる。実測（stream_test の
 * G2 多文、held-out 24 文、CH=8）: n_frames ≡ 4 (mod 8) の文で **12**、≡ 3 で 11、
 * それ以外は 10。上限 12 は 2·CH − (SAAN_LATENCY mod CH) に一致する。
 * ⚠️ **(CH+2) では足りない**（審査 2026-09-03）: ≡ 3・4 (mod 8) の文（held-out 24 文中 7 文）で
 *    1〜2 KB を隣のバッファに書き、1 文の G2（demo は 106 ≡ 2）でも QEMU checksum でも
 *    検出できない（陽性対照で確認: (CH+2) にするとその 7 文だけがコアのガードで落ち、
 *    1 文の G2 は通ったまま）。stream_test の
 *    G2 多文で「超えない **かつ** ちょうど届く」ことまで assert し、コアも
 *    書く前に `ofill >= SAAN_OBUF_HOPS` を見て SAAN_ERR_ARENA で止める。
 * ⚠️ CH を変えたらこの式で動く（CH=16 なら 32 − 4 = 28。S7 の注記）。式は CH=8 の
 *    実測に合っているが、CH=16 での 28 は**未実測**（S7 で G2 多文を回して確かめること） */
#define SAAN_OBUF_HOPS (2 * SAAN_CHUNK - (SAAN_LATENCY % SAAN_CHUNK))

/* 初期化。duration を走らせて d_hat と n_frames を確定させる */
saan_status saan_stream_init(saan_stream *st, const saan_weights *w,
                             saan_arena *a, const int32_t *ids, int32_t n_ids,
                             float s_v);

/* 次のチャンクを取り出す。`*n_out` が 0 になったら発話の終わり。
 * `pcm` は少なくとも SAAN_CHUNK * SAAN_HOP サンプルぶん必要 */
saan_status saan_stream_pull(saan_stream *st, float *pcm, int32_t *n_out);

/* この発話で必要な arena のバイト数（**発話長に依存しない部分**と
 * ids に比例する部分の合計）。G3 の確認に使う */
size_t saan_stream_arena_needed(int32_t n_ids);

/* `saan_stream_init` が **成功したときの `a->used` の値そのもの**（T2 / S9 で導入）。
 * init が確保する全バッファ（log_d / d_hat / impl / パイプ / 作業領域 / iSTFT / obuf / token）の
 * ALIGN16 和で、duration の一時領域（init の中で返す）は含まない。
 *
 * 用途は雛形の二重防御（esp32/main/main.c）: init が SAAN_OK を返した後に `a.used` がこれと
 * **一致しなければ**確保が黙って抜けている。以前は定数 SAAN_ARENA_USED_FLOOR（ホストで測った
 * 値）を置いていたが、`sizeof(struct saan_stream_impl)` がポインタ幅で変わる（ホスト 64 bit /
 * Xtensa 32 bit で 768 B 違う。T2 の QEMU で実際に「a.used が 179,296 B しかない」と拒否された）ので、
 * 各ターゲットで自分の sizeof から計算する関数にした。
 * ⚠️ **確保の一覧を 2 回書いている**（stream_init_body とこの関数）。片方だけ変えると
 *    `make -C csrc arena` の §5（実測 a.used との bit 一致、陽性対照つき）が落ちる。 */
size_t saan_stream_arena_used(int32_t n_ids);

#endif /* SAANOTTS_STREAM_H */
