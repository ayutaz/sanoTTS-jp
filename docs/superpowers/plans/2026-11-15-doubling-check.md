# 二重感の確認 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 第三者の記事が指摘した「声が少し遅れて重なって聞こえる二重感」が、出荷物 v4 の生徒でも起きているかを、**事前に決めた規則**で判定して記録する。

**Architecture:** 道具を 2 つ作る。(1) 参照なしで「遅れた写し」を測るケプストラム型のプローブ。**わざと二重にした教師の音（陽性対照の梯子）で効くことを確かめてから**生徒に当てる。(2) 盲検の聴取セット。生徒・教師・陽性対照を混ぜて番号だけで出し、答えの鍵は別ファイルに置く。**判定の主役は聴取**で、プローブは補助。プローブが陽性対照で効かなければ「判定に使えない」と記録して聴取だけで決める。

**Tech Stack:** Python 3（`numpy` / `soundfile` だけ）/ `uv run --no-project --with numpy --with soundfile`

**Spec:** [`docs/superpowers/specs/2026-11-15-nonkiai-followups-design.md`](../specs/2026-11-15-nonkiai-followups-design.md)

**Branch:** `eval/doubling-check`（`main` から）

## Global Constraints

- **重みもコアも変えない**（測るだけ）
- 素材は **held-out 24 文**（`scripts/eval_student.py` が作る `reports/eval_v4_full/student/` と `teacher/`。どちらも同じパディング経路 = M-61 の G1）
- **陽性対照の梯子**: 教師の音に `gain ∈ {0.1, 0.2, 0.3, 0.5}` × `delay ∈ {10, 20, 30} ms` の遅れた写しを足す（`y + g·y(t − d)`、ピークが 1.0 を超えたら全体を同じ倍率で縮める）
- **判定の規則は聴取の前に D-071 として書いて固定する**（結果を見てから規則を変えない）
- `reports/` の音声は git 管理外。**コーパス本文を追跡物に書かない**（`scripts/sanitize_reports.py` を最後に回す）
- 私は音を聞けない。聴取は人が行い、私は鳴らす・集計するだけ

## Review Focus

1. **無音・短すぎる wav**（有声フレームが 0）→ プローブが例外で落ちずに `n_voiced = 0` を返し、集計から外したことを件数で出す → Task 1 の `silence`
2. **ステレオ / 22,050 Hz 以外の wav** → 黙って読まずに拒否する → Task 1 の `rejects_bad_format`
3. **ピッチの倍音とケプストラムの山の取り違え**（F0 220 Hz の 4.5 ms の倍数）→ 遅れの無い倍音信号で誤検出しない → Task 1 の `harmonic_clean`
4. **聴取者が陽性対照を聞き分けられない**→ その回答を判定に使わない → Task 2 の `listener_not_validated`
5. **答えの鍵を聴取者に見せてしまう**→ 聴取用のディレクトリに鍵を置かない → Task 2 の `key_not_in_listen_dir`

---

### Task 0: 素材を戻す（人と私）

⚠️ **2026-11-15 時点で手元に `runs/v4/stage4.pt`・`~/Documents/piper-plus`・`reports/eval_v4_full/` が無い。**

- [ ] **Step 1: 生徒の重みを落とす**

  Run: `mkdir -p runs/v4 && gh release download v1.2.0 -R ayutaz/sanoTTS-jp -p saanotts-jp-v4-stage4.pt -O runs/v4/stage4.pt && shasum -a 256 runs/v4/stage4.pt`
  Expected: 先頭が `be5bf3f586cc6de7`（CLAUDE.md の値）。

- [ ] **Step 2: piper-plus と教師 ckpt を戻す（人）**

  `~/Documents/piper-plus` を clone し直し、private の `ayousanz/piper-plus-zero-shot-tsukuyomi` から `epoch=499-step=22000.ckpt` を置く。
  Run: `uv sync --extra eval && uv run python scripts/phase0_verify_teacher.py`
  Expected: 6 チェックすべて PASS。

- [ ] **Step 3: 評価音声を作る**

  Run: `uv run --extra eval python scripts/eval_student.py --ckpt runs/v4/stage4.pt --out reports/eval_v4_full`
  Expected: `student/` と `teacher/` に 24 本ずつ。

  （コミットは無い。素材は git 管理外）

---

### Task 1: プローブ（`echo_score`）

**Files:**
- Create: `src/saanotts_jp/doubling.py`
- Create: `scripts/test_doubling.py`
- Modify: `.github/workflows/ci.yml`（`python` job に `uv run --no-project --with numpy --with soundfile python scripts/test_doubling.py`）

**Interfaces:**
- Produces:
  ```python
  SR = 22050
  @dataclass(frozen=True)
  class EchoScore:
      score: float        # 有声フレームの中央値。遅れた写しが強いほど大きい
      n_voiced: int       # 使ったフレーム数（0 なら score は nan）
      peak_ms: float      # 中央値フレームでの山の位置（ms）。n_voiced=0 なら nan
  def add_echo(x: np.ndarray, delay_ms: float, gain: float, sr: int = SR) -> np.ndarray: ...
  def echo_score(x: np.ndarray, sr: int = SR) -> EchoScore: ...
  def load_mono(path: str) -> np.ndarray:   # 22,050 Hz モノラル以外は ValueError
  ```

**アルゴリズム（固定する）:**
- フレーム 2,048 サンプル（92.9 ms）・hop 512・Hann 窓。フレームの RMS が発話全体の最大フレーム RMS より 35 dB 以上低いものは捨てる
- 実ケプストラム `c = irfft(log(|rfft(frame)| + 1e-8))`
- そのフレームのピッチ周期 `T0` = `c` の 2.5〜12.5 ms の範囲の最大位置
- 探索範囲は 8〜40 ms。`k·T0 ± 0.5 ms`（k ≥ 1）に入るビンを**除く**
- フレームの値 = 除いた残りの `|c|` の最大。発話の `score` = 有声フレームの値の中央値

- [ ] **Step 1: 失敗するテストを書く**（`scripts/test_doubling.py`。各テストは `OK` / `NG` を出し、最後に `n 件中 n 件` を出して NG があれば exit 1）

  合成信号は「F0 220 Hz ± 3%（5 Hz のビブラート）、倍音 1〜40 を 1/k で、1.5 s、seed 0 の白色雑音を −40 dB」とする（`_voice()` としてテストの中に書く）。

  | 名前 | 主張 |
  |---|---|
  | `add_echo_impulse` | `add_echo(δ, 20, 0.5)` の 441 サンプル目が 0.5、0 サンプル目が 1.0、それ以外が 0（縮めが要らない大きさで） |
  | `add_echo_rescales` | ピークが 1.0 を超える入力で、出力の `max|y| ≤ 1.0` |
  | `harmonic_clean` | `echo_score(_voice()).score` が `echo_score(add_echo(_voice(), 20, 0.3)).score` の **1/2 未満** |
  | `ladder_monotonic` | 各 `delay ∈ {10, 20, 30}` で `gain = 0, 0.1, 0.2, 0.3, 0.5` の score が**狭義単調増加** |
  | `peak_at_delay` | `add_echo(_voice(), 20, 0.3)` の `peak_ms` が 20 ± 1 ms |
  | `outside_window` | `delay = 5 ms`（探索範囲の外）の score が `delay = 20 ms` の score の 1/2 未満 |
  | `silence` | 全部 0 の 1 s で `n_voiced == 0`、`score` は nan、例外を出さない |
  | `rejects_bad_format` | ステレオの wav と 16 kHz の wav を `load_mono` に渡すと ValueError |

- [ ] **Step 2: 落ちることを確かめる**

  `doubling.py` は関数の宣言だけ（`raise NotImplementedError`）。
  Run: `uv run --no-project --with numpy --with soundfile python scripts/test_doubling.py`
  Expected: 8 件すべて NG（例外は NG として数えて続ける）。

- [ ] **Step 3: 上のアルゴリズムどおりに実装する**

- [ ] **Step 4: 通ることを確かめる**

  Run: 同上
  Expected: `8 件中 8 件`。

- [ ] **Step 5: 除外マスクを外して確かめる（陽性対照。実行したものだけ記録する）**

  一時的に `k·T0 ± 0.5 ms` の除外を外し、`harmonic_clean` が落ちる（ピッチの倍数を遅れと取り違える）ことを見る。落ちなければ `_voice()` の F0 を変えても除外が効いていないので、Step 1 に戻る。結果をコミットメッセージに書いて、元に戻す。

- [ ] **Step 6: Commit**

  ```bash
  git add src/saanotts_jp/doubling.py scripts/test_doubling.py .github/workflows/ci.yml
  git commit -m "feat(eval): 遅れた写しを測るケプストラムのプローブ echo_score（8 件・マスクの陽性対照つき）"
  ```

---

### Task 2: 実データでの検証と盲検の聴取セット

**Files:**
- Create: `scripts/doubling_check.py`
- Create: `scripts/make_doubling_listening.py`
- Modify: `scripts/test_doubling.py`（聴取セットの 3 件を追加）

**Interfaces:**
- Consumes: Task 1 の `echo_score` / `add_echo` / `load_mono`
- Produces:
  - `scripts/doubling_check.py --eval-dir reports/eval_v4_full --out reports/doubling_v4` → `report.json`:
    `{"n": 24, "valid": bool, "teacher": [...], "student": [...], "ladder": {"d20_g0.2": [...], ...}, "student_gt_teacher": int, "sign_test_p": float, "student_vs_ladder": "below_g0.1" | "g0.1-g0.2" | ...}`
  - `scripts/make_doubling_listening.py make --eval-dir reports/eval_v4_full --out reports/doubling_listen --n 12 --seed 0` → `reports/doubling_listen/play/trial_01.wav …`（36 本 = 12 文 × 生徒 / 教師 / 教師 + 遅れ 20 ms・gain 0.3）と `reports/doubling_listen/sheet.tsv`（`trial` と空欄の `重なって聞こえる(y/n)`）。**鍵は `reports/doubling_listen_key.json`**（`play/` の外）
  - `scripts/make_doubling_listening.py score --key reports/doubling_listen_key.json --sheet reports/doubling_listen/sheet.tsv` → 陽性対照の検出数 / 生徒の y 数 / 教師の y 数 / 判定

**判定の規則（D-071 として聴取の前に固定する）:**
- プローブが**有効** = 24 文中 22 文以上で `d20_g0.2` の score > 教師の score
- 聴取者が**有効** = 陽性対照 12 本中 10 本以上で y
- **当てはまる** = 聴取者が有効で、生徒の y が教師の y より 3 本以上多い。プローブが有効なら、補助として「生徒 > 教師」の文数と符号検定の p を併記する
- **当てはまらない** = 聴取者が有効で、上を満たさない
- **判定できない** = 聴取者が有効でない（聴取者を替えてやり直す）

- [ ] **Step 1: 失敗するテストを書く**（`test_doubling.py` に追加。一時ディレクトリに合成した偽の eval-dir を作って回す）

  | 名前 | 主張 |
  |---|---|
  | `key_not_in_listen_dir` | `make` の後、`play/` 以下に `.json` が 1 つも無く、ファイル名から条件が読めない（`trial_NN.wav` だけ） |
  | `listener_not_validated` | 陽性対照の y が 9/12 の回答シートで `score` が「判定できない」を返す |
  | `decides_yes` | 陽性対照 12/12・生徒 y 5・教師 y 1 のシートで「当てはまる」、生徒 y 2・教師 y 1 で「当てはまらない」 |

- [ ] **Step 2: 落ちることを確かめ、実装して通す**

  Run: `uv run --no-project --with numpy --with soundfile python scripts/test_doubling.py`
  Expected: 実装前は 3 件 NG、実装後は `11 件中 11 件`。

- [ ] **Step 3: 実データでプローブを回す**

  Run: `uv run --no-project --with numpy --with soundfile python scripts/doubling_check.py --eval-dir reports/eval_v4_full --out reports/doubling_v4`
  Expected: `report.json` ができ、`valid` が出る。**`valid` が false でも止まらずに記録する**（プローブは補助）。

- [ ] **Step 4: Commit**

  ```bash
  git add scripts/doubling_check.py scripts/make_doubling_listening.py scripts/test_doubling.py
  git commit -m "feat(eval): 二重感の実データ検証と盲検の聴取セット（鍵は play/ の外）"
  ```

---

### Task 3: 規則を固定し、聴いてもらい、記録する

**Files:**
- Modify: `docs/decisions.md`（D-071 = 判定の規則。**聴取の前に**）
- Modify: `docs/measurements.md`（M-152 = プローブの値と聴取の結果）
- Modify: `CLAUDE.md`（残タスク #1 G32 の行に「二重感」を 1 項目足す）

- [ ] **Step 1: 判定の規則を D-071 に書いてコミットする**（聴取の前。結果を見てから変えないため）

  ```bash
  git add docs/decisions.md
  git commit -m "docs: 二重感の判定規則を聴取の前に固定（D-071）"
  ```

- [ ] **Step 2: 聴取セットを作って渡す**

  Run: `uv run --no-project --with numpy --with soundfile python scripts/make_doubling_listening.py make --eval-dir reports/eval_v4_full --out reports/doubling_listen --n 12 --seed 0`
  人に渡すもの: `reports/doubling_listen/play/` と `sheet.tsv`。**鍵は渡さない。** 1 本ずつ `afplay` で鳴らし、各本に y / n を書いてもらう。

- [ ] **Step 3: 集計する**

  Run: `uv run --no-project --with numpy --with soundfile python scripts/make_doubling_listening.py score --key reports/doubling_listen_key.json --sheet reports/doubling_listen/sheet.tsv`
  Expected: D-071 の 3 つのどれかが出る。

- [ ] **Step 4: 記録する**

  M-152 に、プローブの表（教師・生徒・梯子の中央値と `valid`）、聴取の結果、判定を書く。**見ていないもの**（聴取者 n=1 / 実機のスピーカーではない / 摩擦音とアクセントは別問題）も書く。「当てはまる」なら、倍音励起の設計（v5・再学習・速度の要件の見直し）を別の spec として起こすと D-071 に追記する。

- [ ] **Step 5: ゲートを回して PR を作る**

  Run: `uv run python scripts/sanitize_reports.py && uv run python scripts/check_doc_counters.py && uv run python scripts/check_doc_links.py && uv run --no-project python scripts/build_measurements_index.py --check && uv run --no-project python scripts/check_ci_coverage.py`
  Expected: すべて OK（`sanitize_reports.py` は第三者コーパスが無いと exit 2 =「回せなかった」と出る。その場合はそう書く）。

  ```bash
  git add docs/measurements.md CLAUDE.md
  git commit -m "docs: v4 の二重感をプローブと盲検の聴取で判定（M-152）"
  git push -u origin eval/doubling-check
  gh pr create --base main --title "eval: 記事の『二重感』が v4 に当てはまるか（D-071 / M-152）"
  ```
