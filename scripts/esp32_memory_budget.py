#!/usr/bin/env python3
"""ESP32-S3 のメモリ収支を、論文の**実測値を起点に**見積もる。

⚠️ 実機測定ではない。ただし bottom-up の推測でもない。
論文 arXiv:2608.21378 が ESP32-S3 実機で測った arena **289 KB** を起点に、
本プロジェクトとの差分（発話長・端末側 G2P）だけを足し引きする。

なぜ bottom-up をやめたか
-------------------------
最初にテンソル形状から積み上げたところ、論文の 289 KB に対して **586 KB** と
297 KB 超過した。支配項は自分で置いた「重みステージング 316 KB」という仮定で、
これは論文が実機で 289 KB を達成している事実と矛盾する。
つまり**仮定のほうが間違っている**（`docs/decisions.md` C-009 と同じ形の失敗）。

論文と本プロジェクトのグラフは**完全に同一**（567,008 params / 22.05 kHz / hop 256 /
1024 点 iSTFT）。違うのは発話長と、端末側 G2P 951 B があることだけ。
したがって実測値をスケールするほうが正しい。

実行:
    uv run python scripts/esp32_memory_budget.py
"""

from __future__ import annotations

# --- 論文の実測値（arXiv:2608.21378 §V）----------------------------------

PAPER_ARENA = 289 * 1024  # ESP32-S3 実機でのピーク arena
PAPER_SAMPLES = 100_096  # 4.539 s の golden utterance
INT8_BLOBS = 679_832  # int8 blob 2 個（280,288 + 399,544）
PAPER_RTF_S3 = 0.22

SAMPLE_RATE = 22050
HOP = 256
FPS = SAMPLE_RATE / HOP  # 86.13

# --- 本プロジェクトの実測値 ------------------------------------------------

MORA_TABLE_BYTES = 951  # scripts/kana_g2p.py
INTERMEDIATE_AVG_BYTES = 108  # held-out 1,500 文の平均（最大 413 B）
INTERMEDIATE_MAX_BYTES = 413

# 教師で実測した発話長（docs/measurements.md M-5 / M-15）
TEACHER_MEAN_SEC = 2.93  # train コーパス 60 文の平均
EMBEDDED_MEAN_SEC = 65 / INTERMEDIATE_AVG_BYTES * TEACHER_MEAN_SEC  # embedded は短い

# --- ESP32-S3 のハード制約 -------------------------------------------------

S3_INTERNAL_SRAM = 512 * 1024


def kb(n: float) -> str:
    return f"{n / 1024:>7.1f} KB"


def main() -> int:
    print("ESP32-S3 メモリ収支")
    print("⚠️ 実機測定ではない。論文の実測 289 KB を起点にした差分見積もり")
    print("=" * 66)

    # arena を「PCM 出力」と「それ以外」に分解する。
    # PCM は発話長に比例し、それ以外はほぼ一定（フレーム単位で使い回すため）。
    paper_pcm = PAPER_SAMPLES * 2  # int16
    paper_working = PAPER_ARENA - paper_pcm

    print(f"\n■ 論文の 289 KB の内訳（PCM は算出、残りは差分）")
    print("─" * 66)
    print(f"  PCM 出力 int16 ({PAPER_SAMPLES:,} sample)   {kb(paper_pcm)}  発話長に比例")
    print(f"  それ以外の作業領域                  {kb(paper_working)}  ほぼ一定")
    print(f"  合計（論文の実測）                  {kb(PAPER_ARENA)}")
    print(f"\n  → arena の {paper_pcm / PAPER_ARENA * 100:.0f}% は PCM 出力バッファ。"
          f"\n    グラフ本体の作業領域は {paper_working / 1024:.0f} KB しかない")

    print(f"\n■ 本プロジェクトの発話長での arena")
    print("─" * 66)
    print(f"  {'発話':<26}{'PCM':>10}{'作業領域':>11}{'arena':>10}")
    cases = [
        ("embedded 想定 (1.5 s)", 1.5),
        ("train 平均 (2.93 s)", TEACHER_MEAN_SEC),
        ("論文 golden (4.54 s)", PAPER_SAMPLES / SAMPLE_RATE),
        ("最長級 (8 s)", 8.0),
    ]
    for label, sec in cases:
        pcm = round(sec * SAMPLE_RATE) * 2
        print(f"  {label:<26}{kb(pcm)}{kb(paper_working)}{kb(pcm + paper_working)}")

    print(f"\n  I2S へ逐次出力すれば PCM は数 KB のリングで済み、"
          f"\n  発話長によらず **約 {paper_working / 1024:.0f} KB** に固定できる")

    # --- 端末側 G2P の増分 ---
    print(f"\n■ 本プロジェクトが論文に足すもの")
    print("─" * 66)
    g2p_ram = MORA_TABLE_BYTES + INTERMEDIATE_MAX_BYTES * 4  # テーブル + 入出力
    print(f"  mora テーブル (flash 常駐可)        {kb(MORA_TABLE_BYTES)}  実測")
    print(f"  中間表現の入出力バッファ            {kb(INTERMEDIATE_MAX_BYTES * 4)}  最長 413 B から")
    print(f"  RAM 増分                            {kb(g2p_ram)}")
    print(f"  → 論文の {paper_working / 1024:.0f} KB に対して {g2p_ram / paper_working * 100:.1f}%。"
          f" 無視できる")

    # --- 判定 ---
    print(f"\n■ 判定")
    print("─" * 66)
    print(f"  内部 SRAM                           {kb(S3_INTERNAL_SRAM)}")
    print()
    for label, sec, streaming in [
        ("PCM 全保持 / 4.54 s", 4.539, False),
        ("PCM 全保持 / 8 s", 8.0, False),
        ("I2S 逐次出力 / 発話長不問", 0.0, True),
    ]:
        need = paper_working + g2p_ram + (0 if streaming else round(sec * SAMPLE_RATE) * 2)
        head = S3_INTERNAL_SRAM - need
        print(f"  {label:<28}{kb(need)}  SRAM 残 {head/1024:>6.1f} KB")

    print(f"""
  この見積もりの範囲での結論:

    * 論文が実機で 289 KB を達成しており、**グラフは同一**。
      本プロジェクトの増分は G2P の {g2p_ram/1024:.1f} KB だけ
    * arena の 68% は PCM 出力。**I2S へ逐次流せば発話長に依存しなくなる**
    * フラッシュは重み {INT8_BLOBS/1024:.0f} KB + G2P {MORA_TABLE_BYTES} B = 約 {(INT8_BLOBS+MORA_TABLE_BYTES)/1024:.0f} KB。
      8 MB ボードでも問題にならない

  → **メモリを理由に中止する材料は無い。** 蒸留に進んでよい。

  ⚠️ ただし go/no-go はまだ出せない。残るのは:
    1. IDF + FreeRTOS + I2S が内部 SRAM をどれだけ先に食うか（未実測）
    2. アプリの実バイナリサイズ（xtensa ビルド未実施）
    3. 1 発話の実行時間（論文 RTF {PAPER_RTF_S3} は英語 Kristin での実測。
       グラフが同一なので日本語でも同等のはずだが未確認）

  これらは**生徒モデルが出来てから**実機で測る。今この時点で
  「載らないから中止」となる材料が無いことが分かったのが本タスクの成果。""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
