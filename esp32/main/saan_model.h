/* 重み blob の入手（flash → ポインタ 1 本）。
 *
 * **SRAM にコピーしない。** blob は 643,936 B（int8）〜 2,249,792 B（fp32）あり、
 * ESP32-S3 の内部 SRAM 512 KB には入らない。flash を mmap して
 * そのまま読む（コアは blob を書き換えないので read-only で足りる）。
 */
#ifndef SAAN_MODEL_H
#define SAAN_MODEL_H

#include <stdbool.h>
#include <stddef.h>

#include "saanotts.h"

/* `model` パーティションを mmap して saan_weights を開く。
 * 成功したら *w が使える。失敗したら false（理由は ESP_LOGE に出る）。
 *
 * ⚠️ **アライメントを assert する。** コアは payload を const float* に
 *    直接キャストする。Xtensa は非アラインの 4 バイトロードで例外になる。 */
bool saan_model_open(saan_weights *w);

/* mmap を解放する（雛形では呼ばない — 発話ごとに開き直す意味が無い） */
void saan_model_close(void);

#endif /* SAAN_MODEL_H */
