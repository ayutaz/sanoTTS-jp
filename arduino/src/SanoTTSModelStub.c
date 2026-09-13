/* 重みライブラリが入っていないときに `saan_model_open()` を埋める。
 *
 * ⚠️ **リンクを通すためだけのもので、音は出ない。** `SanoTTS::begin()` が
 *    false を返し、`lastError()` が「重みライブラリを入れること」と言う。
 *
 * なぜ要るか: `core/saan_model_rodata.c` は重みライブラリの `saan_model_blob.h` を
 * include するので、入っていない環境では生成器がそのファイルごと `#if` で外す
 * （`scripts/build_arduino_lib.py` の `G_RODATA`）。外した結果 `saan_model_open()` の
 * 定義が 1 つも無くなるので、ここで埋める。
 *
 * ⚠️ **「重みが無いのに黙って動く」形にしないこと。** 無音が出るのではなく、
 *    `begin()` が理由つきで失敗する。
 */
#include "sanotts_config.h"

#if !SANOTTS_MODEL_FROM_PARTITION && !SANOTTS_HAVE_VOICE

#include "core/saan_model.h"

bool saan_model_open(saan_weights *w) {
    (void)w;
    return false;
}

void saan_model_close(void) {}

#endif /* !SANOTTS_MODEL_FROM_PARTITION && !SANOTTS_HAVE_VOICE */
