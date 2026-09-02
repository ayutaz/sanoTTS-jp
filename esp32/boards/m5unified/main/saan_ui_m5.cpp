/* saan_ui.h の M5GFX 実装。設計は saan_ui.h を読むこと。
 *
 * 出所: nnn112358/SanoTTS-jp-M5StackCoreS3（MIT, Copyright (c) 2026 nnn112358）の
 *       main/saan_ui.cpp を、本リポジトリの saan_ui.h に合わせて取り込んだ。
 *
 * 画面の割り当て（320 x 240 横）:
 *   上段  … 文（漢字。シリアル入力のときは無し）
 *   中段  … かな中間表現（合成に使った列そのもの）+ 出典
 *   下段  … ステータス（合成中 / xRT と途切れ回数）
 *
 * フォントは M5GFX 同梱の lgfxJapanGothic_20（IPA ゴシック由来、U8g2 形式）。
 * ⚠️ **フォントは flash (.rodata) に置かれる。** サイズごとに別の配列なので、
 *    使うサイズを増やすとそのぶん app が太る。1 サイズで済ませる。
 * ⚠️ **描画も M5.update() も合成タスクからだけ呼ぶ。** タッチ (I2C) と
 *    スピーカーの AW88298 (I2C) が同じバスなので、別タスクから触らない。 */
#include <M5Unified.h>

#include <stdarg.h>
#include <stdio.h>

#include "esp_log.h"

#include "saan_ui.h"

static const char *TAG = "saan_ui";

/* 出典を常時表示する（モデルの帰属表示の要約。MODEL_CARD.md / LICENSE-MODEL.md） */
#define SAAN_UI_ORIGIN "model: sanoTTS-jp v3 int8  https://github.com/ayutaz/sanoTTS-jp"

static bool s_ready;

/* 下段の開始 y。240 px のうち下 52 px をステータスに使う（2 行） */
#define UI_STATUS_Y 188
#define UI_MARGIN_X 4

static const lgfx::IFont *ui_font(void) { return &fonts::lgfxJapanGothic_20; }

extern "C" {

bool saan_ui_init(void) {
    if (M5.getBoard() == m5::board_t::board_unknown) {
        ESP_LOGE(TAG, "M5.begin() がまだ。saan_audio_setup() の後に呼ぶこと");
        return false;
    }
    auto &d = M5.Display;
    if (d.width() == 0) {
        /* 画面の無い板（Atom など）。UI 無しで続ける = 何もしない実装と同じ挙動 */
        ESP_LOGW(TAG, "画面が無い板。表示とタッチは使わない");
        s_ready = false;
        return true;
    }
    d.fillScreen(TFT_BLACK);
    d.setFont(ui_font());
    d.setTextSize(1);
    d.setTextWrap(true, false);
    s_ready = true;
    ESP_LOGI(TAG, "画面 %d x %d / フォント lgfxJapanGothic_20", (int)d.width(), (int)d.height());
    return true;
}

void saan_ui_show(const char *title, const char *kana) {
    if (!s_ready) return;
    auto &d = M5.Display;
    d.fillRect(0, 0, d.width(), UI_STATUS_Y, TFT_BLACK);
    d.setFont(ui_font());
    d.setTextWrap(true, false);

    d.setCursor(UI_MARGIN_X, 6);
    if (title != NULL && title[0] != '\0') {
        d.setTextColor(TFT_WHITE, TFT_BLACK);
        d.print(title);
        d.print("\n");
        d.setCursor(UI_MARGIN_X, d.getCursorY() + 10);
    }
    d.setTextColor(TFT_CYAN, TFT_BLACK);
    if (kana != NULL) d.print(kana);

    /* Font0 は M5GFX 組み込みの 6x8 ASCII で、日本語フォントと違い flash を食わない */
    d.setFont(&fonts::Font0);
    d.setTextWrap(false, false);
    d.setTextColor(TFT_DARKGREY, TFT_BLACK);
    d.setCursor(UI_MARGIN_X, UI_STATUS_Y - 12);
    d.print(SAAN_UI_ORIGIN);
    d.setFont(ui_font());
}

void saan_ui_status(const char *fmt, ...) {
    if (!s_ready) return;
    char buf[128];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);

    auto &d = M5.Display;
    d.fillRect(0, UI_STATUS_Y, d.width(), d.height() - UI_STATUS_Y, TFT_BLACK);
    d.drawFastHLine(0, UI_STATUS_Y, d.width(), TFT_DARKGREY);
    d.setFont(ui_font());
    d.setTextWrap(true, false);
    d.setTextColor(TFT_YELLOW, TFT_BLACK);
    d.setCursor(UI_MARGIN_X, UI_STATUS_Y + 5);
    d.print(buf);
}

bool saan_ui_poll_touch(void) {
    if (!s_ready) return false;
    M5.update();
    const auto n = M5.Touch.getCount();
    for (size_t i = 0; i < n; ++i) {
        const auto t = M5.Touch.getDetail(i);
        if (t.wasPressed()) {
            ESP_LOGI(TAG, "タッチ x=%d y=%d", (int)t.x, (int)t.y);
            return true;
        }
    }
    /* タッチの無い板（Basic）は物理ボタン A で代用 */
    if (M5.BtnA.wasPressed()) {
        ESP_LOGI(TAG, "ボタン A");
        return true;
    }
    return false;
}

} /* extern "C" */
