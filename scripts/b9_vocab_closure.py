#!/usr/bin/env python3
"""B-9: デプロイ語彙を **mora テーブルの閉包**で凍結する。

`reports/b9_vocab.json` はコーパス 23,454 行の**出現数**を数えた。
だがデバイスは `scripts/kana_g2p.py` の変換器で音素を作るので、
**その変換器が原理的に出力しうる音素の集合（閉包）**が正しい語彙である。
コーパスに偶々出なかった音素を落とすと、実運用で出た瞬間に埋め込み表の
範囲外になって壊れる。

閉包は**データ構造を読んで推論するのではなく、変換器を実際に叩いて**列挙する
（`intermediate_to_phonemes` の分岐 — `ん` の異音 / 長音 `ー` / 無声化 `°` /
助詞 — を漏らさないため）。静的列挙との突き合わせもする。

実行:
    uv run python scripts/b9_vocab_closure.py --out reports/b9_vocab_closure.json
"""

from __future__ import annotations

import argparse
import glob
import json
import sys

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

import kana_g2p as K  # noqa: E402
from piper_plus_g2p.encode import pua  # noqa: E402

CKPT = "epoch=499-step=22000.ckpt"


def snapshot() -> str:
    hits = glob.glob(
        "~/.cache/huggingface/hub/"
        "models--ayousanz--piper-plus-zero-shot-tsukuyomi/snapshots/*/"
    )
    if not hits:
        raise SystemExit("教師 ckpt が HF キャッシュに無い")
    return hits[0]


# --- 1. 閉包を「変換器を叩いて」列挙する ------------------------------------


def executable_closure(table: dict[str, list[str]]) -> tuple[set[str], dict[str, list[str]]]:
    """`intermediate_to_phonemes` を実行して、出力されうる音素を集める。

    網羅する分岐:
      * 素のモーラ / 無声化マーク付きモーラ（全 mora × `°`）
      * `ん` + 後続モーラ（異音 4 種）と `ん` 単独・語末（`N_uvular`）
      * 長音 `ー`（直前の母音を複製）
      * 記号（`MARKS` 全部）
      * 助詞かな（`PARTICLE_KANA` は table に上書き済みなので mora ループで拾う）
    """
    seen: dict[str, list[str]] = {}   # token -> どの入力で出たか（先頭 3 例）

    def run(seq: list[str], why: str) -> None:
        for p in K.intermediate_to_phonemes(seq, table):
            ex = seen.setdefault(p, [])
            if len(ex) < 3 and why not in ex:
                ex.append(why)

    moras = sorted(table)
    for m in moras:
        run([m], f"mora {m}")
        run([m + K.DEVOICED_MARK], f"mora {m}°")
        # 長音: 母音で終わるモーラの直後
        run([m, "ー"], f"{m}ー")
        # `ん` の異音は **後続**で決まる
        run(["ん", m], f"ん+{m}")
        run(["ん", m + K.DEVOICED_MARK], f"ん+{m}°")
    run(["ん"], "ん 単独")
    run(["あ", "ん"], "語末の ん")
    for mk in sorted(K.MARKS):
        run(["あ", mk, "あ"], f"記号 {mk}")
        run(["ん", mk, "か"], f"ん+記号{mk}+か")   # 記号をまたいで異音が決まるか
    return set(seen), seen


def static_closure(table: dict[str, list[str]]) -> set[str]:
    """データ構造から直接列挙（実行版のクロスチェック用）。"""
    out: set[str] = set(K.MARKS)
    for ph in table.values():
        out.update(ph)
        if ph and ph[-1] in K.REVOICE:
            out.add(K.REVOICE[ph[-1]])
    out.update(K.N_ALLOPHONE.values())
    out.add("N_uvular")
    out.update(K.VOWELS)          # 長音 ー は直前の母音を複製する
    return out


# --- 2. パラメータ数（`_param_reference.py` と同じ式）------------------------


def n_duration(V: int, w: int = 32) -> int:
    return V * w + 30 * w * w + 13 * w + 4


def n_acoustic(V: int, w: int = 48, P: int = 88, cdim: int = 40) -> int:
    return V * w + 8 * (10 * w * w + 4 * w) + P * w + cdim * w


def n_decoder(W: int = 76, E: int = 304, K_: int = 7, R: int = 12,
              CIN: int = 40, r: int = 48, OUT: int = 1539) -> int:
    return (
        CIN * W * 3 + W
        + 5 * (W * K_)
        + 5 * (W * E + E)
        + 5 * (E * W + W)
        + 5 * (CIN * R + R)
        + 5 * (R * W + W)
        + 5
        + (W * r + r)
        + (r * OUT + OUT)
    )


def host_reachability() -> dict:
    """閉包にあってコーパスに無い 6 トークンを、**ホスト側 G2P が出せるか**測る。

    出せるなら「コーパスに無かっただけ」＝学習データを足せば埋まる。
    出せないなら埋め込み行が永久に未学習のまま残る。
    """
    from piper_plus_g2p.japanese import JapanesePhonemizer

    ph = JapanesePhonemizer()
    eos_probes = ["本当ですか?", "本当ですか?!", "本当ですか?.", "本当ですか?~",
                  "そうなの?!", "えっ!?"]
    # 非狭母音の無声化を狙う。無声子音に挟まれた a/e/o を持つ語
    aeo_probes = ["かかし", "ほかほか", "ここ", "はかせ", "かっこ", "ぽかぽか", "そこ",
                  "かたかた", "はっか", "各国", "確固", "刻苦", "北海道", "博士",
                  "保管庫", "ホッケー", "カッター", "家庭", "こそこそ", "ぱさぱさ",
                  "かさかさ", "ほくほく", "そこそこ"]
    found: dict[str, list[str]] = {}
    for s in eos_probes + aeo_probes:
        for p in ph.phonemize(s):
            if p in ("?!", "?.", "?~", "A", "E", "O"):
                found.setdefault(p, []).append(s)
    return {
        "n_probes": len(eos_probes) + len(aeo_probes),
        "eos_probes": eos_probes,
        "devoiced_aeo_probes": aeo_probes,
        "emitted": {k: v[:3] for k, v in sorted(found.items())},
        "not_emitted": sorted({"?!", "?.", "?~", "A", "E", "O"} - set(found)),
    }


def token_frame_ratio(pack: str = "data/pack_sibdense") -> tuple[float, int]:
    """L/T（音素ID数 ÷ フレーム数）をラベルパックから実測する。

    トークンレート部の MAC/秒 を出すのに要る。**推定しない**。
    """
    from saanotts_jp.labelpack import PackReader

    nL = nT = n = 0
    for u in PackReader(pack):
        nL += len(u["ids"])
        nT += u["zT"].shape[-1]
        n += 1
    return nL / nT, n


def macs_per_audio_second(dw: int = 32, aw: int = 48, W: int = 76, E: int = 304,
                          R: int = 12, CIN: int = 40, r: int = 48, K_: int = 7,
                          OUT: int = 1539, fps: float = 86.13,
                          lt_ratio: float = 0.4157) -> dict:
    """MAC/audio-second。**埋め込みは表引きなので 0 MAC**（語彙削減で MMAC は減らない）。

    * フレームレート部 = Aβ の frame block 5 段 + 出力 1x1 + Gγ 全体（86.13 fps）
    * トークンレート部 = Dα 全体 + Aβ の token block 3 段（`lt_ratio` × 86.13 /s）
    * iSTFT は 1024pt radix-2 の概算（4 real MAC / complex butterfly）
    """
    ac_frame = 5 * 2 * 5 * aw * aw          # 5 frame block × 2 conv × kernel5
    ac_out = aw * CIN
    dec = (CIN * W * 3 + 5 * (W * K_) + 5 * (W * E) + 5 * (E * W)
           + 5 * (CIN * R) + 5 * (R * W) + W * r + r * OUT)
    dur_tok = 3 * 2 * 5 * dw * dw + dw      # Dα: 3 block × 2 conv × k5 + proj
    ac_tok = 3 * 2 * 5 * aw * aw            # Aβ token block 3 段
    istft = 4 * (1024 // 2) * 10            # radix-2 1024pt の概算
    frame_mmac = (ac_frame + ac_out + dec) * fps / 1e6
    token_mmac = (dur_tok + ac_tok) * lt_ratio * fps / 1e6
    istft_mmac = istft * fps / 1e6
    return {
        "acoustic_frame_MAC_per_frame": ac_frame + ac_out,
        "decoder_MAC_per_frame": dec,
        "duration_MAC_per_token": dur_tok,
        "acoustic_token_MAC_per_token": ac_tok,
        "frame_rate_MMAC": round(frame_mmac, 3),
        "token_rate_MMAC": round(token_mmac, 3),
        "istft_MMAC_estimate": round(istft_mmac, 3),
        "total_MMAC_per_audio_second": round(frame_mmac + token_mmac + istft_mmac, 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/b9_vocab_closure.json")
    ap.add_argument("--corpus", default="reports/b9_vocab.json")
    args = ap.parse_args()

    table = K.build_mora_table()      # ← N_ALLOPHONE に kw/gw/v を足す副作用がある
    exec_set, provenance = executable_closure(table)
    stat_set = static_closure(table)

    snap = snapshot()
    cfg = json.load(open(snap + "config.json"))
    pim = cfg["phoneme_id_map"]
    import torch
    num_symbols = torch.load(snap + CKPT, map_location="cpu",
                             weights_only=False)["hyper_parameters"]["num_symbols"]

    def tok_id(tok: str):
        ch = pua.TOKEN2CHAR.get(tok, tok)
        v = pim.get(ch)
        if v is None:
            return None
        return v[0] if v[0] < num_symbols else ("OOR", v[0])

    closure = exec_set
    closure_missing_in_teacher = sorted(t for t in closure if tok_id(t) is None
                                        or isinstance(tok_id(t), tuple))
    closure_ok = {t: tok_id(t) for t in closure if isinstance(tok_id(t), int)}

    corpus = json.load(open(args.corpus))
    corpus_toks = {u["tok"]: u["id"] for u in corpus["used"]}

    closure_only = sorted(set(closure_ok) - set(corpus_toks))
    corpus_only = sorted(set(corpus_toks) - set(closure_ok))   # ← あれば列挙バグ

    final = dict(sorted({**closure_ok, **corpus_toks}.items(), key=lambda kv: kv[1]))
    V = len(final)

    # --- パラメータ数への影響 ---
    base = {"D": n_duration(157), "A": n_acoustic(157), "G": n_decoder()}
    new = {"D": n_duration(V), "A": n_acoustic(V), "G": n_decoder()}
    base_tot = sum(base.values())
    new_tot = sum(new.values())
    assert base_tot == 567008, base_tot

    # int8 blob: 論文の 679,832 B は 567,008 params に対して +112,824 B。
    # 内訳は論文に無い（未検証）。埋め込み行は純粋な int8 重みなので
    # 削減分は (157-V)*(32+48) B と等しい、という仮定だけを置く。
    blob_paper = 679832
    blob_delta = -(157 - V) * (32 + 48)

    reach = host_reachability()

    # --- width 再配分の選択肢 ---
    freed = base_tot - new_tot
    lt, n_pack = token_frame_ratio()
    baseline_mm = macs_per_audio_second(lt_ratio=lt)
    options = []

    def opt(name, D_w, A_w, dec_kw, simd, note):
        d, a = n_duration(V, D_w), n_acoustic(V, A_w)
        g = n_decoder(**dec_kw)
        mm = macs_per_audio_second(
            dw=D_w, aw=A_w, lt_ratio=lt,
            **{k: v for k, v in dec_kw.items() if k in ("W", "E", "R", "r")})
        options.append({
            "name": name, "D_width": D_w, "A_width": A_w,
            "decoder_overrides": dec_kw or "default (W=76,E=304,R=12,r=48)",
            "D_alpha": d, "A_beta": a, "G_gamma": g, "total": d + a + g,
            "vs_567008": d + a + g - 567008,
            "MMAC_per_audio_second": mm["total_MMAC_per_audio_second"],
            "MMAC_delta_vs_O0": round(mm["total_MMAC_per_audio_second"]
                                      - baseline_mm["total_MMAC_per_audio_second"], 3),
            "simd_16lane_aligned": simd,
            "note": note,
        })

    opt("O0 浮かせたまま（推奨）", 32, 48, {}, True,
        f"埋め込みを {V} 行にするだけ。{freed:,} params / 約 {freed:,} B の flash が浮く。"
        "MMAC は 1 も減らない（埋め込みは表引き）。ESP32-S3 で逼迫するのは flash ではなく"
        "実時間（論文 0.22x RT）なので、浮いた分を計算に変えると割が悪い")
    opt("O1 Aβ の幅を 48→49", 32, 49, {}, False,
        "浮いた 8,000 をほぼ使い切って acoustic の frame block を 4.2% 厚くする。"
        "ただし 49 は 16 レーンの PIE int8 に割り切れず、実効スループットは落ちる可能性")
    opt("O2 decoder の条件付けランク R を 12→16", 32, 48, {"R": 16}, True,
        "16 の倍数を保ったまま入る唯一の増強。c-line から decoder への FiLM 経路を"
        "33% 広げる。40ch の c を 12 に絞る箇所がボトルネックなら効く")
    opt("O3 Dα の幅を 32→35", 35, 48, {}, False,
        "duration はトークンレート（L/T = %.3f 実測）なので MMAC への効き目が最小。"
        "アクセント型ミニマルペアが duration で決まるなら投資先として妥当" % lt)

    out = {
        "task": "B-9 closure: デプロイ語彙を mora テーブルの閉包で凍結する",
        "repro": "uv run python scripts/b9_vocab_closure.py --out reports/b9_vocab_closure.json",
        "method": (
            "kana_g2p.build_mora_table() を作り、intermediate_to_phonemes() を "
            "全 mora × {素, °, +ー, ん+後続, 記号} で実行して出力トークンを集める。"
            "データ構造からの静的列挙とも突き合わせる。"
        ),
        "mora_table": {
            "entries": len(table),
            "bytes": K.table_size_bytes(table),
            "n_allophone_rules": len(K.N_ALLOPHONE),
        },
        "closure": {
            "n": len(closure),
            "tokens": sorted(closure),
            "executable_vs_static_diff": {
                "exec_only": sorted(exec_set - stat_set),
                "static_only": sorted(stat_set - exec_set),
            },
            "missing_in_teacher_phoneme_id_map": closure_missing_in_teacher,
        },
        "teacher": {"num_symbols": num_symbols,
                    "phoneme_id_map_entries": len(pim),
                    "snapshot": snap.rstrip("/").split("/")[-1]},
        "corpus": {
            "source": args.corpus,
            "n_rows": corpus["n_rows"], "n_ok": corpus["n_ok"],
            "n_rejected": corpus["n_rejected"],
            "n_used": corpus["n_used"],
        },
        "diff": {
            "closure_only": [{"tok": t, "id": closure_ok[t],
                              "seen_via": provenance.get(t, [])[:3]} for t in closure_only],
            "corpus_only_BUG_IF_NONEMPTY": [{"tok": t, "id": corpus_toks[t]}
                                            for t in corpus_only],
            "closure_only_host_reachability": reach,
            "closure_only_verdict": (
                "閉包のみの 6 個は全部『コーパスに無かっただけ』。"
                "?! ?. ?~ はホスト G2P が実際に出す（原文に該当の約物があれば）。"
                "A/E/O はプローブ 23 語では出なかったが、中間表現に `°` を書けば"
                "デバイスは必ず出す（か° -> k A）。**語彙には入れる。"
                "ただし埋め込み行が未学習のままになるので、コーパスに"
                "?! ?. ?~ を含む行を足す必要がある**"
                "（CLAUDE.md の多様性軸に『4 種の EOS』が挙がっているのに"
                "現行 23,454 行には 1 つも無い）"
            ),
        },
        "final_vocab": {
            "V": V,
            "definition": "閉包 ∪ コーパス出現（教師の先頭 173 に存在するものだけ）",
            "teacher_id_gaps": sorted(set(range(max(final.values()) + 1))
                                      - set(final.values())),
            "teacher_id_gaps_note": (
                "生徒の埋め込みは教師IDでは引けない（穴がある）。"
                "teacher_id -> student_index の写像表を重みと一緒に凍結すること。"
                "穴は長音記号 a: i: u: e: o:（ー は直前母音の複製にしている）、"
                "素の N（異音 4 種に分解している）、q、zy（じゃ行は j に落ちる）"
            ),
            "teacher_id_to_student_index": {str(i): k
                                            for k, (t, i) in enumerate(final.items())},
            "tokens": [{"student_index": k, "tok": t, "teacher_id": i,
                        "corpus_n": next((u["n"] for u in corpus["used"]
                                          if u["tok"] == t), 0)}
                       for k, (t, i) in enumerate(final.items())],
            "zero_count_in_corpus": [t for t, i in final.items()
                                     if not any(u["tok"] == t for u in corpus["used"])],
        },
        "param_impact": {
            "paper_VOCAB": 157,
            "measured_V": V,
            "formula": {
                "D_alpha": "V*32 + 30*32^2 + 13*32 + 4",
                "A_beta": "V*48 + 80*48^2 + 160*48",
                "G_gamma": "V に依存しない",
            },
            "baseline": {**base, "total": base_tot},
            "with_measured_V": {**new, "total": new_tot},
            "delta": {"D_alpha": new["D"] - base["D"], "A_beta": new["A"] - base["A"],
                      "G_gamma": 0, "total": new_tot - base_tot,
                      "check_(157-V)*80": -(157 - V) * 80},
            "int8_blob": {
                "paper_bytes": blob_paper,
                "paper_bytes_note": (
                    "論文値。567,008 params に対し +112,824 B あり、内訳は論文に無い"
                    "（未検証。scale/zero-point と int32 bias が疑わしいが未測定）"
                ),
                "delta_bytes": blob_delta,
                "delta_assumption": "埋め込み行は 1 param = 1 B の int8 重み",
                "estimated_bytes": blob_paper + blob_delta,
            },
        },
        "mmac": {
            "baseline": baseline_mm,
            "paper_claim_MMAC_per_audio_second": 45,
            "L_over_T_measured": round(lt, 4),
            "L_over_T_source": f"data/pack_sibdense の {n_pack} 発話（ids 総数 ÷ zT 総フレーム）",
            "note": (
                "自前計算。論文の 45 MMAC/audio-second に対し "
                f"{baseline_mm['total_MMAC_per_audio_second']} で、"
                "`_param_reference.py` の層構成が論文の compute とも整合することの傍証。"
                "**埋め込みは表引きなので 0 MAC = 語彙を 157→"
                f"{V} にしても MMAC は 1 も減らない**（減るのは flash だけ）。"
                "iSTFT 分は radix-2 の概算で未測定"
            ),
        },
        "width_options": options,
        "caveats": [
            "閉包は kana_g2p.py の現行実装に対するもの。mora テーブルや "
            "N_ALLOPHONE / FOREIGN_MORA を変えたら測り直しが要る",
            "コーパス側の数値は reports/b9_vocab.json（23,454 行）をそのまま使った",
            "int8 blob の内訳は論文に無く、削減分の仮定は未検証",
            "`fy` は閉包に入らない。mora テーブルに ふゅ が無く、教師の "
            "phoneme_id_map にも fy が無い。55 行はホスト側 "
            "phonemes_to_intermediate の逆引きで落ちる（デバイス側の問題ではない）",
            "width 再配分案のパラメータ数は式で出し、"
            "`_param_reference.py` の torch モジュールと一致することを確認済み。"
            "ただし**品質への効果は未測定**（どこに配ると効くかは学習しないと分からない）",
            "SIMD アライメントの可否は ESP32-S3 PIE の 128bit = int8 16 レーンから"
            "判断したもので、実機カーネルでの実効スループットは未測定",
        ],
    }
    json.dump(out, open(args.out, "w"), ensure_ascii=False, indent=2)

    print(f"mora テーブル {len(table)} エントリ / {K.table_size_bytes(table)} B "
          f"/ ん 異音 {len(K.N_ALLOPHONE)} 件")
    print(f"閉包 {len(closure)} トークン")
    print("  教師に無い:", closure_missing_in_teacher or "なし")
    print(f"  実行列挙 - 静的列挙: {sorted(exec_set - stat_set) or 'なし'}")
    print(f"  静的列挙 - 実行列挙: {sorted(stat_set - exec_set) or 'なし'}")
    print(f"コーパス使用 {corpus['n_used']} / 閉包のみ {len(closure_only)}: "
          f"{' '.join(closure_only) or 'なし'}")
    print(f"コーパスのみ（=列挙バグ） {len(corpus_only)}: "
          f"{' '.join(corpus_only) or 'なし'}")
    print(f"  ホスト G2P が出せた（n={reach['n_probes']} プローブ）: "
          f"{' '.join(reach['emitted']) or 'なし'} / "
          f"出なかった: {' '.join(reach['not_emitted']) or 'なし'}")
    print(f"\n最終語彙 V = {V}")
    print(f"  Dα {base['D']:,} -> {new['D']:,}  ({new['D']-base['D']:+,})")
    print(f"  Aβ {base['A']:,} -> {new['A']:,}  ({new['A']-base['A']:+,})")
    print(f"  Gγ {base['G']:,} -> {new['G']:,}  (+0)")
    print(f"  合計 {base_tot:,} -> {new_tot:,}  ({new_tot-base_tot:+,})")
    print(f"  int8 blob {blob_paper:,} B -> {blob_paper+blob_delta:,} B "
          f"({blob_delta:+,} B, 仮定つき)")
    print(f"\nMMAC/audio-second（自前計算, L/T={lt:.4f} 実測 n={n_pack}）: "
          f"{baseline_mm['total_MMAC_per_audio_second']}  ← 論文 45")
    print("  内訳: frame %.2f / token %.2f / iSTFT %.2f（概算）"
          % (baseline_mm["frame_rate_MMAC"], baseline_mm["token_rate_MMAC"],
             baseline_mm["istft_MMAC_estimate"]))
    print()
    for o in options:
        print(f"  {o['name']:<26} total {o['total']:>7,} "
              f"({o['vs_567008']:+6,} vs 567,008)  "
              f"MMAC/s {o['MMAC_per_audio_second']:6.2f} ({o['MMAC_delta_vs_O0']:+.2f})  "
              f"SIMD16 {'o' if o['simd_16lane_aligned'] else 'x'}")
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
