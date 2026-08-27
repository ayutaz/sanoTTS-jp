/* saanoTTS-jp 推論コア（C99 / 依存なし）
 *
 * 論文 arXiv:2608.21378 の 3 段構成をそのまま実装する:
 *
 *   音素ID → Duration Dα → d̂ = clip[1,80](round(s_v·r))
 *          → Acoustic Aβ → c[40,T]
 *          → iSTFT Decoder Gγ → 22.05 kHz PCM
 *
 * 設計方針:
 *   - **malloc をコアで呼ばない。** 呼び出し側が arena を渡す（ESP32 で断片化させない）
 *   - 重みは SAAN 形式のバイナリを mmap / flash から読む（コピーしない）
 *   - fp32 でまず正しさを出す。int8 カーネルは golden test が通ってから
 *
 * ⚠️ **参照実装（`src/saanotts_jp/_param_reference.py`）と 1:1 で対応させること。**
 *   片方だけ直すと golden test が落ちる。それが検出手段。
 */
#ifndef SAANOTTS_H
#define SAANOTTS_H

#include <stddef.h>
#include <stdint.h>

#define SAAN_SR        22050
#define SAAN_HOP       256
#define SAAN_NFFT      1024
#define SAAN_NBINS     513      /* NFFT/2 + 1 */
#define SAAN_CDIM      40       /* c-line */
#define SAAN_DUR_W     32
#define SAAN_AC_W      48
#define SAAN_DEC_W     76
#define SAAN_DEC_E     304
#define SAAN_DEC_R     12       /* 条件付けのランク */
#define SAAN_DEC_HEAD  48
#define SAAN_POS_MAX   88       /* 音素内位置埋め込み */
#define SAAN_VOCAB     57       /* 日本語のデプロイ語彙（D-016） */
#define SAAN_CLIP_LO   1
#define SAAN_CLIP_HI   80
#define SAAN_S_V       1.2187f  /* D-019 */

typedef enum {
    SAAN_OK = 0,
    SAAN_ERR_MAGIC = -1,        /* SAAN ヘッダでない */
    SAAN_ERR_VERSION = -2,
    SAAN_ERR_MISSING = -3,      /* 必要なテンソルが無い */
    SAAN_ERR_SHAPE = -4,        /* shape が想定と違う */
    SAAN_ERR_ARENA = -5,        /* arena が足りない */
    SAAN_ERR_RANGE = -6         /* 音素ID が語彙外 */
} saan_status;

/* 重みブロブ（読み取り専用。コアは書き換えない） */
typedef struct {
    const uint8_t *base;
    size_t size;
    uint32_t n_tensors;
} saan_weights;

/* 作業領域。**コアは malloc しない** */
typedef struct {
    uint8_t *buf;
    size_t size;
    size_t used;
} saan_arena;

/* 1 発話の中間結果へのポインタ（すべて arena 上） */
typedef struct {
    int32_t n_ids;
    int32_t n_frames;
    int32_t n_samples;
    float *log_d;       /* [n_ids] */
    int32_t *d_hat;     /* [n_ids] */
    float *c;           /* [SAAN_CDIM * n_frames] */
    float *pcm;         /* [n_samples] */
} saan_output;

/* --- API ---------------------------------------------------------------- */

saan_status saan_weights_open(saan_weights *w, const void *blob, size_t size);

/* 名前でテンソルを引く。見つからなければ NULL。`dims` は 4 要素書かれる */
const void *saan_tensor(const saan_weights *w, const char *name,
                        uint32_t *dtype, uint32_t dims[4], uint64_t *nbytes);

void saan_arena_init(saan_arena *a, void *buf, size_t size);
void saan_arena_reset(saan_arena *a);

/* n_ids トークンを合成するのに必要な arena バイト数（上限）。
 * **実際に走らせる前にこれで足りるか確かめる**（ESP32 で OOM しないため） */
size_t saan_arena_needed(int32_t n_ids);

/* 合成。`ids` は**生徒の埋め込みインデックス**（教師IDではない。D-016） */
saan_status saan_synthesize(const saan_weights *w, saan_arena *a,
                            const int32_t *ids, int32_t n_ids,
                            float s_v, saan_output *out);

const char *saan_strerror(saan_status s);

#endif /* SAANOTTS_H */
