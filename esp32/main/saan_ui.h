/* 画面とタッチの抽象 API。
 *
 * 実装は 2 つ:
 *   saan_ui_null.c                        何もしない（DevKit / QEMU / ホスト stub）
 *   boards/m5unified/main/saan_ui_m5.cpp  M5GFX（上段: 文 / 中段: かな / 下段: ステータス）
 *
 * main.c は実装を知らずにこの 4 関数を呼ぶ。**無くても喋る**（画面は補助）。
 * ⚠️ M5 実装では描画も M5.update() も**合成タスクからだけ**呼ぶこと。タッチ (I2C) と
 *    スピーカーの AW88298 (I2C) が同じバスなので、別タスクから触らない。 */
#ifndef SAAN_UI_H
#define SAAN_UI_H

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* saan_audio_setup() の**後**に呼ぶ（M5 実装は M5.begin() 済みを前提にする） */
bool saan_ui_init(void);

/* 文（title。NULL 可）と かな中間表現を表示する */
void saan_ui_show(const char *title, const char *kana);

/* ステータス行を書き直す（printf 書式） */
void saan_ui_status(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

/* 押された**瞬間**があれば true。押しっぱなしでは繰り返さない */
bool saan_ui_poll_touch(void);

#ifdef __cplusplus
}
#endif
#endif /* SAAN_UI_H */
