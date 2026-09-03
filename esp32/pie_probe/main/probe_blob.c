/* 本物の重み blob（student_i8.bin）を .rodata（flash）に埋める翻訳単位。
 *
 * 生成ヘッダ saan_model_blob.h は esp32/cmake/saan_model_rodata.cmake が
 * scripts/blob_to_header.py でビルドディレクトリに作る（出荷ファームの M5 構成と同じ仕組み。
 * `const uint8_t g_saan_model_blob[]` + `__attribute__((aligned(16)))`）。
 * ⚠️ この翻訳単位は SAAN_PROBE_HAVE_BLOB=1 のときだけビルドに入る（main/CMakeLists.txt）。 */
#include "probe_blob.h"

#include "saan_model_blob.h"

const uint8_t *probe_blob(size_t *n) {
    *n = (size_t)SAAN_MODEL_BLOB_BYTES;
    return g_saan_model_blob;
}

const char *probe_blob_sha256(void) { return SAAN_MODEL_BLOB_SHA256; }
