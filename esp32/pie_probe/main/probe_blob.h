/* D 節（重みの置き場所）が使う本物の重み blob。
 *
 * ビルドに blob が埋まっていれば（CMake の SAAN_PROBE_HAVE_BLOB=1。probe_blob.c が
 * scripts/blob_to_header.py の生成ヘッダを include する）その先頭と大きさを返し、
 * 埋まっていなければ NULL を返す。probe.c はそれを見て D 節を skip する。
 * ⚠️ **配列の定義を持つ生成ヘッダを include するのは probe_blob.c だけ**（重複定義になる）。 */
#ifndef PROBE_BLOB_H
#define PROBE_BLOB_H

#include <stddef.h>
#include <stdint.h>

#ifndef SAAN_PROBE_HAVE_BLOB
#define SAAN_PROBE_HAVE_BLOB 0
#endif

#if SAAN_PROBE_HAVE_BLOB
const uint8_t *probe_blob(size_t *n);
const char *probe_blob_sha256(void);
#else
static inline const uint8_t *probe_blob(size_t *n) { *n = 0; return NULL; }
static inline const char *probe_blob_sha256(void) { return "-"; }
#endif

#endif /* PROBE_BLOB_H */
