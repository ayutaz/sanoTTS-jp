/* saan_ui.h の「何もしない」実装。DevKit（画面が無い）/ QEMU / ホスト stub 用。 */
#include "saan_ui.h"

bool saan_ui_init(void) { return true; }
void saan_ui_show(const char *title, const char *kana) { (void)title; (void)kana; }
void saan_ui_status(const char *fmt, ...) { (void)fmt; }
bool saan_ui_poll_touch(void) { return false; }
