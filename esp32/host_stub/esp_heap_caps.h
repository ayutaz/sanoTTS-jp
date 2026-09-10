/* ホスト stub — ヒープ計測は意味を持たないので 0 を返す。
 * ⚠️ **だからホストの数字を「実機の SRAM 実測」と読んではいけない。** */
#ifndef SAAN_STUB_ESP_HEAP_CAPS_H
#define SAAN_STUB_ESP_HEAP_CAPS_H
#include <stddef.h>
#define MALLOC_CAP_INTERNAL 0x1
#define MALLOC_CAP_8BIT     0x2
static inline size_t heap_caps_get_free_size(unsigned c) { (void)c; return 0; }
static inline size_t heap_caps_get_largest_free_block(unsigned c) { (void)c; return 0; }
/* 起動からの低水位。⚠️ **ホストでは 0。** PSRAM の無い板で Open JTalk の
 * 一時ヒープのピークを捉えるのに使う（M-98）が、それは QEMU / 実機の値であって
 * ここで出る 0 ではない。 */
static inline size_t heap_caps_get_minimum_free_size(unsigned c) { (void)c; return 0; }
#define MALLOC_CAP_SPIRAM   0x4
/* stubs.c が malloc/free で実装する。⚠️ PSRAM と内部の区別は無い（どちらも通る） */
void *heap_caps_malloc(size_t n, unsigned caps);
void *heap_caps_aligned_alloc(size_t align, size_t n, unsigned caps);
void  heap_caps_free(void *p);
#endif
