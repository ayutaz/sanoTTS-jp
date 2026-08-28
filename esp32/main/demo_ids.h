/* 自動生成 — 手で編集しない。
 *
 *   uv run python scripts/gen_demo_ids.py
 *
 * 出典: csrc/golden.bin の in.ids（golden.bin sha256 は csrc/export.json）
 * 原文: 今日は良い天気ですね。
 *
 * ⚠️ これは**生徒インデックス**（0..56）であって教師の音素ID ではない。
 *    生徒は教師の ID 空間を直接使えない（D-016 / src/saanotts_jp/vocab.py）。
 *
 *
 * SAAN_DEMO_INTERMEDIATE が**端末が実際に受け取る入力**で、
 * csrc/g2p.c の saan_g2p() に通すと kSaanDemoIds になる。
 * ⚠️ kSaanDemoIds は**答え合わせの錨**であって入力ではない。
 *    esp32/main/main.c は G2P の出力の方を合成に使う。
 */
#ifndef SAAN_DEMO_IDS_H
#define SAAN_DEMO_IDS_H

#include <stdint.h>

#define SAAN_DEMO_TEXT "今日は良い天気ですね。"

/* 端末側 G2P の入力。ひらがな + `[` 上昇 / `]` 下降核 / `#` 句境界 / `°` 無声化 */
#define SAAN_DEMO_INTERMEDIATE "きょ][おわよ][いて][んきです°ね"
#define SAAN_DEMO_INTERMEDIATE_BYTES 44
#define SAAN_DEMO_N_IDS 53

/* 音素列: ^ _ ky _ o _ ] _ [ _ o _ w _ a _ y _ o _ ] _ [ _ i _ t _ e _ ] _ [ _ N_ng _ k _ i _ d _ e _ s _ U _ n _ e _ $
 */
static const int32_t kSaanDemoIds[SAAN_DEMO_N_IDS] = {
     1,  0, 26,  0, 14,  0,  9,  0,  8,  0, 14,  0,
    55,  0, 10,  0, 56,  0, 14,  0,  9,  0,  8,  0,
    11,  0, 31,  0, 13,  0,  9,  0,  8,  0, 22,  0,
    25,  0, 11,  0, 33,  0, 13,  0, 41,  0, 17,  0,
    49,  0, 13,  0,  2,
};

#endif /* SAAN_DEMO_IDS_H */
