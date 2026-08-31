"""sys.dic への実アクセスを記録する計装を入れる（64 KiB ページ / 32 B ライン）。"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os
SP = (_WORK + "")

# --- 1. 共通ヘッダ ---
hdr = os.path.join(_WORK, "oj/mecab/src/saan_touch.h")
open(hdr, "w").write('''#ifndef SAAN_TOUCH_H
#define SAAN_TOUCH_H
#include <stddef.h>
/* sys.dic のマッピング内アクセスを記録する。base 外のアドレスは無視する。 */
extern const char *g_saan_dic_base;
extern size_t      g_saan_dic_size;
extern unsigned char *g_saan_pg;    /* 64 KiB ページのビットマップ(1B/page) */
extern unsigned char *g_saan_ln;    /* 32 B ラインのビットマップ(1B/line)  */
extern unsigned long  g_saan_pg_n, g_saan_ln_n;
static inline void saan_touch(const void *p, size_t len) {
  if (!g_saan_dic_base || !g_saan_pg) return;
  const char *c = (const char *)p;
  if (c < g_saan_dic_base || c >= g_saan_dic_base + g_saan_dic_size) return;
  size_t off = (size_t)(c - g_saan_dic_base);
  size_t end = off + len;
  if (end > g_saan_dic_size) end = g_saan_dic_size;
  for (size_t o = off; o < end; o += 32) { g_saan_ln[o >> 5] = 1; g_saan_pg[o >> 16] = 1; }
  g_saan_ln[(end - 1) >> 5] = 1; g_saan_pg[(end - 1) >> 16] = 1;
}
#endif
''')
print("wrote", hdr)

# --- 2. darts.h の array_ アクセス ---
p = os.path.join(_WORK, "oj/mecab/src/darts.h")
s = open(p, encoding="utf-8").read()
if "saan_touch.h" not in s:
    s = s.replace("#include <cstdio>", '#include <cstdio>\n#include "saan_touch.h"', 1) \
        if "#include <cstdio>" in s else '#include "saan_touch.h"\n' + s
    # commonPrefixSearch の 3 箇所 + exactMatchSearch
    s = s.replace("      p = b;  // + 0;\n      n = array_[p].base;",
                  "      p = b;  // + 0;\n      saan_touch(&array_[p], sizeof(array_[0]));\n      n = array_[p].base;")
    s = s.replace("      p = b +(node_u_type_)(key[i]) + 1;\n      if ((array_u_type_) b == array_[p].check)",
                  "      p = b +(node_u_type_)(key[i]) + 1;\n      saan_touch(&array_[p], sizeof(array_[0]));\n      if ((array_u_type_) b == array_[p].check)")
    s = s.replace("    p = b;\n    n = array_[p].base;",
                  "    p = b;\n    saan_touch(&array_[p], sizeof(array_[0]));\n    n = array_[p].base;")
    open(p, "w", encoding="utf-8").write(s)
    print("patched darts.h  (saan_touch:", s.count("saan_touch(&array_"), "箇所)")

# --- 3. dictionary.h の token() / feature() ---
p = os.path.join(_WORK, "oj/mecab/src/dictionary.h")
s = open(p, encoding="utf-8").read()
if "saan_touch.h" not in s:
    s = s.replace('#include "mmap.h"', '#include "mmap.h"\n#include "saan_touch.h"', 1)
    s = s.replace("  const Token *token(const result_type &n) const {",
                  "  const Token *token(const result_type &n) const {\n"
                  "    saan_touch(token_ + (n.value >> 8), sizeof(Token) * (0xff & n.value));")
    s = s.replace("  const char  *feature(const Token &t) const { return feature_ + t.feature; }",
                  "  const char  *feature(const Token &t) const {\n"
                  "    saan_touch(feature_ + t.feature, 64);\n"
                  "    return feature_ + t.feature; }")
    open(p, "w", encoding="utf-8").write(s)
    print("patched dictionary.h")

# --- 4. dictionary.cpp で base を登録 ---
p = os.path.join(_WORK, "oj/mecab/src/dictionary.cpp")
s = open(p, encoding="utf-8").read()
if "g_saan_dic_base =" not in s:
    s = s.replace("  const char *ptr = dmmap_->begin();",
                  "  const char *ptr = dmmap_->begin();\n"
                  "  if (dmmap_->file_size() > 50000000) {   /* sys.dic だけを対象にする */\n"
                  "    g_saan_dic_base = dmmap_->begin(); g_saan_dic_size = dmmap_->file_size();\n"
                  "    g_saan_pg_n = (g_saan_dic_size >> 16) + 2; g_saan_ln_n = (g_saan_dic_size >> 5) + 2;\n"
                  "    g_saan_pg = (unsigned char *)calloc(g_saan_pg_n, 1);\n"
                  "    g_saan_ln = (unsigned char *)calloc(g_saan_ln_n, 1);\n"
                  "  }", 1)
    s = s.replace('#include "dictionary.h"', '#include "dictionary.h"\n#include <cstdlib>', 1)
    open(p, "w", encoding="utf-8").write(s)
    print("patched dictionary.cpp")
