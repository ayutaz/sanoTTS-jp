#include "saan_dict.h"

#include <inttypes.h>
#include <stdint.h>
#include <string.h>

#include "sdkconfig.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "esp_timer.h"

/* --- 貼り方の選択 -----------------------------------------------------------
 *
 * ⚠️ **`esp_partition_mmap` は 8 MB より大きい辞書を貼れない板がある。**
 *    `CONFIG_SPI_FLASH_ROM_IMPL=y` のとき、ESP32-S3 では `spi_flash_mmap` が
 *    **ROM の実装**にリンクされ（`esp32s3.rom.ld`: `spi_flash_mmap = 0x40000bac`。
 *    IDF 側の実装は `flash_mmap.c` の
 *    `#if !ESP_ROM_HAS_SPI_FLASH_MMAP || !CONFIG_SPI_FLASH_ROM_IMPL` で消える）、
 *    IDF は起動時に `spi_flash_mmap_page_num_init(128)` でそれに
 *    **128 ページ = 8 MB のプールしか渡さない**（components/spi_flash/flash_ops.c）。
 *    辞書は 0xD30000 / 0x10000 = **211 ページ**なので、vaddr がいくら余っていても
 *    足りない。M5Stack の sdkconfig.defaults はこれを y にしている
 *    （内部 DRAM を空けるため）ので、そのままでは辞書が開けない。
 *
 * `esp_mmu_map()`（component `esp_mm`。`esp_mmu_map_init()` は ROM_IMPL に
 * 関わらず cpu_start.c から必ず呼ばれる）は IDF 側の vaddr プール全体から取るので、
 * この上限に当たらない。⚠️ **DRAM は増えない**
 * （`CONFIG_SPI_FLASH_ROM_IMPL=n` に戻す案は IRAM = DRAM を削る）。
 *
 * ⚠️ **両方を同時に使うと危ない。** ROM 実装と IDF 実装は vaddr の割り当てを
 *    互いに知らない。このファームで flash を貼るのは
 *    (a) 重み（`saan_model.c` の `esp_partition_mmap`。M5 は `.rodata` 埋め込みなので使わない）
 *    (b) この辞書
 *    の 2 か所だけで、ROM 実装が選ばれる構成（= M5）では (a) を使っていない。
 *    重みをパーティションに戻すなら、**両方を esp_mmu_map に寄せること。**
 *
 * 既定は「ROM 実装が選ばれているなら esp_mmu_map」。DevKit（ROM_IMPL=n）は
 * 従来どおり `esp_partition_mmap` で、経路は 1 バイトも変わらない。
 * `-DSAAN_DICT_MMU=1 / 0` で明示的に上書きできる。 */
#ifndef SAAN_DICT_MMU
#  if defined(CONFIG_SPI_FLASH_ROM_IMPL) && CONFIG_SPI_FLASH_ROM_IMPL
#    define SAAN_DICT_MMU 1
#  else
#    define SAAN_DICT_MMU 0
#  endif
#endif

#if SAAN_DICT_MMU
#include "esp_mmu_map.h"

#if SAAN_DICT_INTEGRITY_PROBE
/* ⚠️ **測定専用**（残タスク 8 の材料）。既定ビルドには 1 バイトも入らない。 */
#include "esp_crc.h"
#include "mbedtls/sha256.h"
#endif
#endif

static const char *TAG = "saan_dict";

/* partitions_16mb.csv / boards/m5unified/partitions.csv の `dict` 行と一致させること */
#define SAAN_DICT_PART_LABEL   "dict"
#define SAAN_DICT_PART_SUBTYPE 0x41

#if SAAN_DICT_MMU
static void *s_vaddr;
#else
static esp_partition_mmap_handle_t s_handle;
#endif
static bool s_mapped;

bool saan_dict_open(jdict_t *d) {
    const esp_partition_t *part = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA, SAAN_DICT_PART_SUBTYPE, SAAN_DICT_PART_LABEL);
    if (part == NULL) {
        ESP_LOGE(TAG, "パーティション '%s' が無い。**16 MB 版の表を焼いたか？** "
                      "（esp32/partitions_16mb.csv / boards/m5unified/partitions.csv）",
                 SAAN_DICT_PART_LABEL);
        return false;
    }
    ESP_LOGI(TAG, "dict パーティション: offset 0x%08" PRIx32 " size %" PRIu32 " B",
             (uint32_t)part->address, (uint32_t)part->size);

    const void *ptr = NULL;

#if SAAN_DICT_MMU
    /* ⚠️ **返り値は必ず出す。** 「辞書が開けない」だけでは
     *    vaddr 不足（ESP_ERR_NOT_FOUND）と内部ヒープ不足（ESP_ERR_NO_MEM）と
     *    引数不正（ESP_ERR_INVALID_ARG）を切り分けられない。 */
    {
        size_t freeblk = 0;
        esp_err_t ferr = esp_mmu_map_get_max_consecutive_free_block_size(
            MMU_MEM_CAP_READ | MMU_MEM_CAP_8BIT, MMU_TARGET_FLASH0, &freeblk);
        ESP_LOGI(TAG, "esp_mmu_map を使う（CONFIG_SPI_FLASH_ROM_IMPL の 128 ページ制限を回避）"
                      " / 連続空き vaddr %u B (%s)",
                 (unsigned)freeblk, esp_err_to_name(ferr));
    }
    void *v = NULL;
    esp_err_t err = esp_mmu_map((esp_paddr_t)part->address, part->size,
                                MMU_TARGET_FLASH0,
                                MMU_MEM_CAP_READ | MMU_MEM_CAP_8BIT,
                                ESP_MMU_MMAP_FLAG_PADDR_SHARED, &v);
    /* ESP_ERR_INVALID_STATE は「同じ物理範囲が既に貼られていて、その vaddr を返した」 */
    if ((err != ESP_OK && err != ESP_ERR_INVALID_STATE) || v == NULL) {
        ESP_LOGE(TAG, "esp_mmu_map 失敗: %s (0x%x)"
                      "（NOT_FOUND = vaddr の連続空きが足りない / "
                      "NO_MEM = 内部ヒープが足りない）",
                 esp_err_to_name(err), (unsigned)err);
        return false;
    }
    if (err == ESP_ERR_INVALID_STATE)
        ESP_LOGW(TAG, "esp_mmu_map: 既に貼られている領域を共有した（%s）",
                 esp_err_to_name(err));
    s_vaddr = v;
    ptr = (const void *)v;
    ESP_LOGI(TAG, "esp_mmu_map OK: vaddr %p", ptr);
#else
    esp_err_t err = esp_partition_mmap(part, 0, part->size,
                                       ESP_PARTITION_MMAP_DATA, &ptr, &s_handle);
    if (err != ESP_OK || ptr == NULL) {
        /* ⚠️ **ここは MMU の窓が足りないと落ちる。** S3 の vaddr は 32 MB を
         *    flash と PSRAM で分け合う。model 768 KB + dict 13.5 MB を同時に
         *    貼るので、PSRAM を大きく使う構成では足りなくなる（K-0 / D-042）。
         *    ⚠️ CONFIG_SPI_FLASH_ROM_IMPL=y なら 8 MB で頭打ちになるので、
         *       そちらは上の esp_mmu_map 経路（-DSAAN_DICT_MMU=1）へ。 */
        ESP_LOGE(TAG, "esp_partition_mmap 失敗: %s (0x%x)"
                      "（MMU の窓が足りない可能性。model と dict を同時に貼っている。"
                      "CONFIG_SPI_FLASH_ROM_IMPL=y なら 128 ページ = 8 MB が上限）",
                 esp_err_to_name(err), (unsigned)err);
        return false;
    }
    ESP_LOGI(TAG, "esp_partition_mmap OK: vaddr %p (%s)", ptr, esp_err_to_name(err));
#endif
    s_mapped = true;

    if (((uintptr_t)ptr & 15u) != 0u) {
        ESP_LOGE(TAG, "辞書が 16 バイト境界に無い (ptr=%p)", ptr);
        return false;
    }
    /* ⚠️ **渡しているのはパーティション長であって blob 長ではない。**
     *    dict パーティション 13,828,096 B に対し実 blob は 13,702,320 B で、
     *    境界検査が 125,776 B ゆるい。jdict_open は**セクション表から実 extent を
     *    復元して d->blob_len に入れる**ので（M-100）、以後はそちらが効く。 */
    int r = jdict_open(d, (const uint8_t *)ptr, part->size);
    if (r != 0) {
        /* ⚠️ **エラーごとに言い分けないと切り分けられない。** 以前は 1 行だった。 */
        const char *why =
            (r == JDICT_ERR_MAGIC)   ? "先頭が K1D1 でない（焼いていない / 別物）" :
            (r == JDICT_ERR_VERSION) ? "blob の版がこのファームに合わない（辞書を焼き直すこと）" :
            (r == JDICT_ERR_MATRIX)  ? "接続行列が無い / 長さが合わない（辞書が壊れている）" :
            (r == JDICT_ERR_SECTAB)  ? "セクション表が壊れている（焼き損ね）" :
                                       "必須セクションが無い";
        ESP_LOGE(TAG, "jdict_open: %d — %s", r, why);
        return false;
    }
#if SAAN_DICT_INTEGRITY_PROBE
    /* ⚠️ **測定専用。** 13.7 MB の完全性検査に何 ms かかるかを 3 通りで測る
     *    （残タスク 8 = M-100 §8 の 1・2 を塞ぐかの判断材料）。
     * ⚠️ **1 回目は flash から読む / 2 回目以降はキャッシュが効きうる**ので、
     *    **読むだけ → CRC32 → SHA-256 の順に 1 回ずつ**測り、最後に読むだけをもう 1 度測って
     *    「キャッシュが効いたか」を見る。 */
    {
        const uint8_t *b = (const uint8_t *)ptr;
        const size_t n = d->blob_len;
        ESP_LOGW(TAG, "=== 完全性検査コストの測定（%u B）===", (unsigned)n);

        int64_t t0 = esp_timer_get_time();
        uint32_t acc = 0;
        for (size_t i = 0; i + 4 <= n; i += 4) {
            uint32_t v; memcpy(&v, b + i, 4); acc += v;
        }
        int64_t t_read = esp_timer_get_time() - t0;
        ESP_LOGW(TAG, "  1) 読むだけ（32bit 加算）: %.1f ms  （%.1f MB/s）acc=0x%08" PRIx32,
                 (double)t_read / 1000.0,
                 (double)n / 1048576.0 / ((double)t_read / 1e6), acc);

        t0 = esp_timer_get_time();
        uint32_t crc = esp_crc32_le(0, b, n);
        int64_t t_crc = esp_timer_get_time() - t0;
        ESP_LOGW(TAG, "  2) CRC32（ROM）: %.1f ms  （%.1f MB/s）crc=0x%08" PRIx32,
                 (double)t_crc / 1000.0,
                 (double)n / 1048576.0 / ((double)t_crc / 1e6), crc);

        t0 = esp_timer_get_time();
        uint8_t dig[32];
        mbedtls_sha256_context sh;
        mbedtls_sha256_init(&sh);
        if (mbedtls_sha256_starts(&sh, 0) == 0
            && mbedtls_sha256_update(&sh, b, n) == 0
            && mbedtls_sha256_finish(&sh, dig) == 0) {
            int64_t t_sha = esp_timer_get_time() - t0;
            ESP_LOGW(TAG, "  3) SHA-256（mbedtls / HW 支援）: %.1f ms  （%.1f MB/s）"
                          "先頭 %02x%02x%02x%02x%02x%02x%02x%02x",
                     (double)t_sha / 1000.0,
                     (double)n / 1048576.0 / ((double)t_sha / 1e6),
                     dig[0], dig[1], dig[2], dig[3], dig[4], dig[5], dig[6], dig[7]);
        } else {
            ESP_LOGE(TAG, "  3) SHA-256 が失敗した");
        }
        mbedtls_sha256_free(&sh);

        t0 = esp_timer_get_time();
        acc = 0;
        for (size_t i = 0; i + 4 <= n; i += 4) {
            uint32_t v; memcpy(&v, b + i, 4); acc += v;
        }
        int64_t t_read2 = esp_timer_get_time() - t0;
        ESP_LOGW(TAG, "  4) 読むだけ（2 回目 = キャッシュの効き）: %.1f ms"
                      "  （1 回目の %.0f%%）",
                 (double)t_read2 / 1000.0, 100.0 * (double)t_read2 / (double)t_read);
        ESP_LOGW(TAG, "=== 測定おわり（⚠️ このビルドは出荷物ではない）===");
    }
#endif

    /* ⚠️ **行列の形式を必ず出す。** これが無いと、辞書を差し替えたつもりで
     *    差し替わっていない状態（= 前後で差が出ない）を**区別できない**。
     *    実際に affine の速度を測るとき、blob 長で判別するはめになった（M-105）。 */
    ESP_LOGI(TAG, "辞書 OK: 見出し語 %" PRIu32 " / エントリ %" PRIu32
                  " / 行列 %ux%u（%s）/ blob %u B（パーティション %u B。余り %u B）",
             d->n_surfaces, d->n_entries, (unsigned)d->lsize, (unsigned)d->rsize,
             /* ⚠️ **3 形式ある。** かつて 2 値で書いていて、matrixc の辞書を焼いても
              *    「matrixa」と表示していた（M-106 §11 で気づいた）。
              *    **起動ログは焼き間違いを見つけるためにある**ので、嘘をつくと役に立たない。 */
             d->matrix     ? "生 int16"
             : d->matrix_rmap ? "matrixc = 行・列クラスタ + 代表行列"
                              : "matrixa = 行ごとアフィン uint8",
             (unsigned)d->blob_len, (unsigned)part->size,
             (unsigned)(part->size - d->blob_len));
    return true;
}

void saan_dict_close(void) {
    if (!s_mapped) return;
#if SAAN_DICT_MMU
    esp_err_t err = esp_mmu_unmap(s_vaddr);
    if (err != ESP_OK)
        ESP_LOGW(TAG, "esp_mmu_unmap: %s", esp_err_to_name(err));
    s_vaddr = NULL;
#else
    esp_partition_munmap(s_handle);
#endif
    s_mapped = false;
}
