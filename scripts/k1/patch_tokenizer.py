"""Allocator に計測用カウンタを差し込む（scratchpad のコピーのみを触る）。"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os

SP = (_WORK + "")
p = os.path.join(_WORK, "oj/mecab/src/tokenizer.h")
s = open(p, encoding="utf-8").read()

if "g_saan_new_node" in s:
    print("already patched")
    raise SystemExit(0)

old_ns = "namespace MeCab {\n\nclass Param;"
assert old_ns in s, "namespace anchor not found"
s = s.replace(old_ns,
    "namespace MeCab {\n\n"
    "/* === keisoku (saanoTTS-jp) === */\n"
    "extern unsigned long g_saan_new_node;\n"
    "extern unsigned long g_saan_new_path;\n"
    "extern unsigned long g_saan_char_bytes;\n\n"
    "class Param;", 1)

old = "  N *newNode() {\n    N *node = node_freelist_->alloc();"
assert old in s, "newNode anchor not found"
s = s.replace(old, "  N *newNode() {\n    ++g_saan_new_node;\n    N *node = node_freelist_->alloc();", 1)

old = "  P *newPath() {\n    if (!path_freelist_.get()) {"
assert old in s, "newPath anchor not found"
s = s.replace(old, "  P *newPath() {\n    ++g_saan_new_path;\n    if (!path_freelist_.get()) {", 1)

old = "  char *alloc(size_t size) {\n    if (!char_freelist_.get()) {"
assert old in s, "alloc anchor not found"
s = s.replace(old, "  char *alloc(size_t size) {\n    g_saan_char_bytes += (size + 1);\n    if (!char_freelist_.get()) {", 1)

open(p, "w", encoding="utf-8").write(s)
print("patched", p)
