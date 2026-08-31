/* K-1 辞書バイナリの読み出しと K-2 の Viterbi。
 *
 * 設計は docs/plan/k1-kanji-implementation-plan.md K-2。
 * blob の形式は src/saanotts_jp/k1_dict.py が作るもの（magic "K1D1"）。
 *
 * 規約（csrc の他のコアと同じ）:
 *   - 依存は libc の一部だけ。malloc をコアで呼ばない。作業領域は arena で渡す
 *   - blob は mmap した領域をそのまま指す（コピーしない）
 */
#ifndef K1DICT_H
#define K1DICT_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    const uint8_t *blob;
    size_t         blob_len;

    const uint8_t *louds_bits;   /* LOUDS ビット列 */
    uint32_t       louds_bitlen;
    const uint8_t *labels;       /* ノードのラベル（1 B/node） */
    uint32_t       n_nodes;
    const uint8_t *term_bits;    /* 終端フラグ */
    const uint8_t *rank_sup;     /* rank1 の superblock（256 bit ごと u32） */
    const uint8_t *rank_blk;     /* 同 block（64 bit ごと u8） */
    const uint8_t *sel0;         /* select0 の標本（512 個ごと u32） */
    uint32_t       n_sel0;
    const uint8_t *surfck;       /* 見出し語 32 個ごとの累積エントリ数 u32 */

    const uint8_t *counts;       /* 見出し語ごとのエントリ数 */
    uint32_t       n_surfaces;

    const uint8_t *records;      /* 9 B 固定 */
    uint32_t       n_entries;
    const uint8_t *pool;
    uint32_t       pool_len;

    const uint8_t *classes;      /* 8 B: lc u16, rc u16, pos6 u16, posid u16 */
    uint32_t       n_classes;

    const int16_t *matrix;       /* 接続コスト。flat[rc_prev + lsize*lc_cur] */
    uint16_t       lsize, rsize;

    const uint8_t *keytab;       /* NUL 区切りの文字表（1 B 符号） */
    uint32_t       keytab_len;
    const uint8_t *keyesc;       /* 同・副表 */
    uint32_t       keyesc_len;
    const uint8_t *moratab;      /* NUL 区切りのモーラ表（read / pron の復号） */
    uint32_t       moratab_len;
    const uint8_t *chaintab;     /* NUL 区切りのアクセント結合規則 */
    uint32_t       chaintab_len;
    const uint8_t *pos6tab;      /* NUL 区切りの "品詞,細分類1..3,活用型,活用形" */
    uint32_t       pos6tab_len;
    const uint8_t *poolck;       /* 32 エントリごとの値プール offset u32 */
    const uint8_t *termck;       /* 512 ノードごとの累積終端数 u32 */
    uint32_t       n_termck;

    /* K-3: 未知語 */
    const uint8_t *char_names;   /* 32 B ずつのカテゴリ名 */
    uint32_t       n_char_cats;
    const uint8_t *char_info;    /* 65,535 件の CharInfo (u32) */
    uint32_t       n_codepoints;
    const uint8_t *unk;          /* 未知語エントリ（可変長） */
    uint32_t       unk_len;
    uint32_t       n_unk;
} k1_dict_t;

typedef struct { uint32_t len; uint32_t rank; } k1_hit_t;
/* entry の最上位ビットが立っていたら **未知語**で、下位は unk エントリの番号。 */
#define K1_UNKNOWN_FLAG 0x80000000u

typedef struct { uint32_t begin, end, entry; } k1_token_t;

/* blob を開く。0 で成功、負でエラー。 */
int k1_open(k1_dict_t *d, const uint8_t *blob, size_t n);

/* 文字列（UTF-8）を鍵バイト列に符号化する。out_n は入出力。0 で成功。 */
int k1_encode_key(const k1_dict_t *d, const uint8_t *utf8, size_t n,
                  uint8_t *out, size_t *out_n);

/* key[start..] の接頭辞のうち見出し語になっているものを列挙。件数を返す。 */
int k1_common_prefix_search(const k1_dict_t *d, const uint8_t *key, size_t key_n,
                            size_t start, k1_hit_t *out, int max_out);

/* 見出し語 rank のエントリ範囲。 */
void k1_entry_range(const k1_dict_t *d, uint32_t rank,
                    uint32_t *first, uint32_t *count);

/* エントリの接続情報。 */
void k1_entry_conn(const k1_dict_t *d, uint32_t entry,
                   uint16_t *lc, uint16_t *rc, int16_t *wcost);

/* 遷移コスト。⚠️ 索引は flat[rc_prev + lsize*lc_cur]（K-1 §9-3）。 */
int16_t k1_trans(const k1_dict_t *d, uint16_t rc_prev, uint16_t lc_cur);

/* 見出し語 rank の表層形を UTF-8 で書き出す。バイト数を返す（負でエラー）。
 * ⚠️ **LOUDS を親へ遡って組み立てる。** 見出し語の文字列表は blob に無い
 *    （鍵そのものが表層形なので冗長。K-1）。 */
int k1_surface_of_rank(const k1_dict_t *d, uint32_t rank, char *out, size_t out_n);

/* 鍵バイト列の [from, to) を UTF-8 に戻す。バイト数を返す（負でエラー）。
 * ⚠️ **トークンの begin/end は「鍵」の上の位置**で、元テキストの位置ではない。
 *    表層形はここで作る。 */
int k1_key_to_utf8(const k1_dict_t *d, const uint8_t *key, size_t from, size_t to,
                   char *out, size_t out_n);

/* エントリ entry の MeCab feature 文字列を組む（surface は呼び出し側が渡す）。
 *
 *   "表層,品詞,細分類1,細分類2,細分類3,活用型,活用形,原形,読み,発音,アクセント,結合規則"
 *
 * これを `mecab2njd()` にそのまま渡せる。バイト数を返す（負でエラー）。 */
int k1_entry_feature(const k1_dict_t *d, uint32_t entry,
                     const char *surface, char *out, size_t out_n);

/* 未知語ノード（entry の最上位ビットが立っているもの）の feature 文字列。
 * ⚠️ **未知語は 8 列しか無い**（読み/発音/acc/結合規則が無い）。
 *    これがそのまま「無音で消える」の入口（B-0）。 */
int k1_unk_feature(const k1_dict_t *d, uint32_t entry,
                   const char *surface, char *out, size_t out_n);

/* 未知語の読みを**推測して** 12 列の feature を作る。
 * 0 以上で成功（バイト数）、負なら推測できない（呼び出し側が
 * `k1_unk_feature` に落ちる）。
 *
 * ⚠️ **これは正しさではなく「無音で消えない」ための措置。**
 *    未知語は読み/発音を持たないので `njd_set_pronunciation` が読点に
 *    置換し、**語が丸ごと音から消える**（B-0 / M-73）。
 *    推測が当たる保証はない — 落ちる漢字語はほぼ地名で、
 *    単漢字の組み合わせでは当たらない。
 *
 * 規則は 2 つだけ:
 *   - 表層が全部かなら、**それ自身が読み**（カタカナに寄せる）
 *   - そうでなければ、**1 文字ずつ辞書を引いて読みを繋ぐ**
 *     （同じ字に複数あれば単語コスト最小）
 * アクセントは **平板（0）**に逃げる。 */
int k1_unk_guess(const k1_dict_t *d, uint32_t entry,
                 const char *surface, char *out, size_t out_n);

/* Viterbi。arena は作業領域。返り値はトークン数、負でエラー。 */
int k1_analyze(const k1_dict_t *d, const uint8_t *key, size_t key_n,
               void *arena, size_t arena_n, k1_token_t *out, int max_out);

/* 陰性対照用: 接続コストを全部 0 にして解析する（G7）。 */
int k1_analyze_nocost(const k1_dict_t *d, const uint8_t *key, size_t key_n,
                      void *arena, size_t arena_n, k1_token_t *out, int max_out);

#endif
