"""環境の疎通確認: 後処理 7 段を手で組み直して、既定経路と bit 一致するか。"""
import copy, inspect, time
import pyopenjtalk as P
from pyopenjtalk import make_label, modify_filler_accent, predict_nani_reading
from pyopenjtalk.utils import (
    modify_acc_after_chaining,
    modify_kanji_yomi,
    process_odori_features,
    retreat_acc_nuc,
    suppress_unnatural_auxiliary_u_long_vowel,
)

print("pyopenjtalk:", P.__file__)
print("_MULTI_READ_KANJI_SET_EXCLUDING_NANI:",
      len(P._MULTI_READ_KANJI_SET_EXCLUDING_NANI))
for f in (modify_filler_accent, predict_nani_reading, modify_kanji_yomi,
          suppress_unnatural_auxiliary_u_long_vowel, retreat_acc_nuc,
          modify_acc_after_chaining, process_odori_features, make_label):
    print(" ", f.__name__, inspect.signature(f))

texts = [
    "今日は良い天気ですね。",
    "午後3時15分です。",
    "何をしているのですか？",
    "和歌山県太地町に行きました。",
    "彼は人生の効果を鼻血で気づかず。",
]

with P._resolve_jtalk(None) as jt:
    for t in texts:
        raw = jt.run_frontend(t)
        njd = copy.deepcopy(raw)
        njd = modify_filler_accent(njd)
        njd = predict_nani_reading(njd)
        njd = modify_kanji_yomi(t, njd, P._MULTI_READ_KANJI_SET_EXCLUDING_NANI)
        njd = suppress_unnatural_auxiliary_u_long_vowel(njd)
        njd = retreat_acc_nuc(njd)
        njd = modify_acc_after_chaining(njd)
        njd = process_odori_features(njd, jtalk=jt)
        mine = make_label(njd, jtalk=jt)
        ref = P.extract_fullcontext(t)
        van = P.extract_fullcontext(t, use_vanilla=True)
        print(f"{t!r:40s} manual==default: {mine == ref}  vanilla==default: {van == ref}")
        # 生の raw が壊れていないか（in-place 変異の検査）
        print("    raw unchanged after chain:", raw == jt.run_frontend(t))

# 速度
t0 = time.perf_counter()
with P._resolve_jtalk(None) as jt:
    for _ in range(20):
        for t in texts:
            P.extract_fullcontext(t)
print("default extract_fullcontext: %.2f ms/文" % ((time.perf_counter()-t0)/100*1000))
