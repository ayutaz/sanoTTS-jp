"""tokenizer.cpp の lookup() に CPS ヒット数のカウンタを足す。"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from k1_paths import ROOT as _ROOT, WORK as _WORK, PP as _PP  # noqa: E402
import os

SP = (_WORK + "")

# --- ヘッダ側に extern を足す ---
h = os.path.join(_WORK, "oj/mecab/src/tokenizer.h")
s = open(h, encoding="utf-8").read()
if "g_saan_cps_hits" not in s:
    anchor = "extern unsigned long g_saan_char_bytes;\n"
    assert anchor in s
    s = s.replace(anchor, anchor +
                  "extern unsigned long g_saan_cps_hits;    /* CPS が返した key 数 */\n"
                  "extern unsigned long g_saan_lookup_calls;/* lookup() 呼び出し回数 */\n"
                  "extern unsigned long g_saan_cps_max;     /* 1 回の CPS の最大ヒット数 */\n", 1)
    open(h, "w", encoding="utf-8").write(s)
    print("patched", h)
else:
    print("header already patched")

# --- lookup() 本体 ---
c = os.path.join(_WORK, "oj/mecab/src/tokenizer.cpp")
s = open(c, encoding="utf-8").read()
if "g_saan_cps_hits" in s:
    print("tokenizer.cpp already patched")
    raise SystemExit(0)

old = """    const size_t n = (*it)->commonPrefixSearch(
        begin2,
        static_cast<size_t>(end - begin2),
        daresults, results_size);

    for (size_t i = 0; i < n; ++i) {"""
assert old in s, "commonPrefixSearch anchor not found"
new = """    const size_t n = (*it)->commonPrefixSearch(
        begin2,
        static_cast<size_t>(end - begin2),
        daresults, results_size);

    ++g_saan_lookup_calls;
    g_saan_cps_hits += n;
    if (n > g_saan_cps_max) g_saan_cps_max = n;

    for (size_t i = 0; i < n; ++i) {"""
s = s.replace(old, new, 1)
open(c, "w", encoding="utf-8").write(s)
print("patched", c)
