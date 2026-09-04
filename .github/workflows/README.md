# CI に入れているもの / 入れていないもの

**入れてよいのは「新規 clone + 公開リリースの資産だけで通るゲート」だけ。**
どれが通るかは推測せず、**素の clone + 依存ゼロの venv で 1 本ずつ実測して決めた。**

⚠️ **かつて「新規 clone *だけ* で通るもの」と書いていたが、もう正確ではない。**
`golden` と `web` はリリースの重みを落とす。線引きは
「**公開されているものだけで足りるか**」（コーパス・ラベルパック・private ckpt・
凍結 sys.dic・ESP-IDF は公開されていないので入らない）。

## 入れてある（[`ci.yml`](ci.yml)。**6 job**）

| job | 中身 | 依存 | 実測 |
|---|---|---|---:|
| `docs` | 索引の M/D/C 番号・**引用アンカー**・件数 / md の相対リンク / hook の回帰 100 ケース / 本文検出 / blob → .rodata ヘッダ変換 / **ゲートが CI で回るか、回らないなら理由が書いてあるか**（`check_ci_coverage.py`。**陽性対照 2 件**つき。2026-09-03 追加） | **なし**（stdlib のみ） | **9 s** |
| **`golden`** | **参照実装との一致**。**fp32**（`make -C csrc test`）と **int8**（`int8-golden` / `int8`）の両方 + `arena`。重みはリリース **`v0.3.0`** の 4 資産を落とす（2.2 + 0.8 + 0.65 + 0.8 MB）。**陽性対照つき**（重みを壊すと fp32 / int8 の両方で落ちる = C-056） | ネットワーク + cc | **未計測**（int8 レーンは 2026-09-03 追加） |
| `csrc` | `line` `fft` `pad` `g2p` `erf`（GELU の erf 近似 vs libm）**`range`**（S9 の範囲版カーネル vs `[0,T)` 版。2026-09-03 追加）。**どれも重み blob を要らない** | cc のみ | **163 s** |
| `python` | `test_losses`（26 項目）/ `test_labelpack` | torch（**CPU ビルド**）+ numpy | **15 s** |
| `release-assets` | **ドキュメントが名前を挙げた資産がリリースに在るか**（C-052 の再発防止） | ネットワーク | **10 s** |
| **`web`** | **`bash web/build.sh`** + **wasm ゲート 8 本**（G-W7 / G-W1 / G-W2 / **G-W2b** / G-W3 / G-W4 / G-W5 / **G-W6**。`scripts/check_web_gates.sh`）。emcc を **6.0.9 に固定**して入れ、重みは `golden` と同じ `v0.3.0` の資産 | ネットワーク + emsdk | **未計測**（W トラックで追加） |

実測は run `33718551954`（2026-09-03、ubuntu-latest、uv のキャッシュあり）。
⚠️ **`golden` の int8 レーンと `web` job は、この run より後に足したので時間を測っていない。**
⚠️ `csrc` が 26 → 163 s に伸びたのは **`erf` の全 float 走査**（[-4, 4] の 2,164,260,866 値。T5）と `range` の追加による。
再現: `gh run view <id> --json jobs -q '.jobs[] | "\(.name) \(.startedAt) \(.completedAt)"'`

⚠️ **torch は `--torch-backend cpu` を明示している。** 外すと Linux で
CUDA 版（数 GB）を引いて `python` job が数分になる。

### ⚠️ 重みのタグを `v0.3.0` に固定した

`golden` / `web` の `gh release download` はタグ無し（= `latest`）ではなく **`v0.3.0` 固定**。
latest 追従だと **リリースを切った瞬間に、無関係な PR の CI の意味が黙って変わる**。

⚠️ **代償**: 新しいリリースの重みは CI で 1 度も試されない。
**リリースを出したら `ci.yml` のタグを上げること。** latest 側は `release-assets` job が見ている。

## 初回で移植性バグを 1 件見つけた

`csrc` が Linux で落ちた。**CI の設定ではなくコードの欠陥**だった:

```
fft_test.c:27: error: 'M_PI' undeclared
fft_test.c:56: error: 'CLOCK_MONOTONIC' undeclared
```

`M_PI` は C99 の `<math.h>` に無い（POSIX 拡張）。macOS では既定で見えるが、
Linux + glibc の厳密 `-std=c99` では見えない。**出荷するコア 2 本
（`saanotts.c` / `saanotts_stream.c`）も同じ穴を持っていた**（C-052 の追記 / C-033）。

**1 つの OS でしか通していないビルドは「通る」と言えない。**

## 入れていない（**理由つき**）

| ゲート | なぜ入らないか |
|---|---|
| ~~`make -C csrc golden` / `int8-golden` / `int8` / `arena`~~ | ⚠️ **入れた。** 「CI で毎回 2.9 MB 落とすのは割に合わない」と書いていたが、`golden` job が実際に落として回している（fp32 2.2 + 0.8 MB、int8 0.65 + 0.8 MB） |
| `make -C csrc stream` / `int8-e2e` | **`ids_heldout.bin` が要る**（コーパス由来。git にもリリースにも無い） |
| `make -C csrc prof` | **重み blob が要る**。⚠️ リリースから落とせるので `golden` job に足せる（**未着手**。手元では 1.5 s） |
| `scripts/test_discriminator.py` | **ラベルパックが要る**（`data/pack_sib*`。コーパス由来なので配布しない） |
| `scripts/kana_g2p.py` | **pyopenjtalk が要る**（凍結テーブルとの突き合わせは live 側が要る） |
| `make -C csrc jdict` / `accent` / `njd-rules` / `oj-heap` / `kanji-e2e` / `label-ids` | **辞書 13.7 MB と pyopenjtalk が要る**。`all-test` にも入れていないのと同じ理由 |
| `scripts/phase0_verify_teacher.py` | **教師 ckpt が private** |
| ESP-IDF ビルド / QEMU | toolchain が重く、**実機の代わりにならない**（QEMU はサイクル精度ではない） |

⚠️ **「CI が緑」は「正しい」ではない。** ここで見ているのは
**ドキュメントの整合と、C / Python が参照実装と同じ数を出すかだけ**。
**品質（SCOREQ / DNSMOS）・速度・音は 1 つも見ていない。**
⚠️ ゴールデンテストは **1 文（53 ids / 106 frames）**しか通さない。
held-out 24 文を見る `stream` は下記の理由で回らない。

## CI で回せないゲートと、その理由

**`scripts/check_ci_coverage.py` が機械的に検査する**（`docs` job）。
「CI で回る」か「`EXCLUDED_TARGETS` / `EXCLUDED_SCRIPTS` に理由が書いてある」かのどちらかであることを要求し、
**理由の表が実体とずれても落ちる**（消えたゲートの言い訳が残っていても NG）。

⚠️ **コメントは「回っている」ではない**（2026-09-03 に直した）。
それまでは **`ci.yml` のコメントにゲート名を書いただけ**で「CI で回っている」判定になった。
理由の表から外すときに「コメントで言及した」だけで通る = **空虚なゲート**だったので、
`strip_comments()` で走らない行を先に落とすようにし、
**陽性対照を 2 件目として足した**（`--self-test`。CI の実行行をコメントに変えたら NG になる）。

回せない理由は 3 つに集約される:

| 理由 | 何が回せないか |
|---|---|
| **コーパス由来の成果物が git にもリリースにも無い** | `stream` の多文レーン / `int8-e2e`（`ids_heldout.bin`）/ `g2p-corpus` / `test_discriminator.py`（ラベルパック） |
| **辞書 13.7 MB と pyopenjtalk が要る** | 漢字経路の 6 ゲート（`jdict` / `accent` / `njd-rules` / `oj-heap` / `kanji-e2e` / `label-ids`）と `kb-parity` |
| **ESP-IDF の xtensa toolchain（約 2 GB）** | `check_esp32_template.sh` |

⚠️ **訂正（C-057）: ここには 4 つ目として「重み blob の int8 版が古い」があった。**
「リリースの `saanotts-jp-v3-int8.bin` は **v1** で、S4 以降のコアが `SAAN_ERR_VERSION` で拒む。
v2 を配れば CI に入れられる」と書いていたが、**これは v0.2.0 までの話。**
**v0.3.0 の int8 資産は blob v2**（654,032 B。SHA-256 の頭は `2d2b8543`）で、
手元の v2 blob と bit 一致する（`docs/release-notes/v0.3.0.md` も v2 と書いている）。
→ `int8` / `int8-golden` は **`golden` job で回るようになった**。
残っていた `int8-e2e` の理由は blob ではなく **`ids_heldout.bin`** の方だった。

⚠️ **`stream` の多文 G2（held-out 24 文 × fp32 / W8A32 / W8A8 の bit 一致）が CI で回らないのは痛い。**
速度の作り直し（T1〜T5 / S5b）を守っていたのはこのゲートで、今は手元と QEMU でしか回らない。
`ids_heldout.bin` は 12,384 B しかないが、**音素 ID からコーパス本文がある程度復元できる**ので
コミットしていない（`csrc/label_ids_bss.txt` を ignore しているのと同じ理由）。

## ⚠️ 陽性対照は blob ごとに作り直すこと（int8 で実際に空虚だった）

`golden` job の陽性対照は「重みを壊したらゴールデンテストが落ちる」を見る。
**fp32 用に書いた壊し方を int8 にそのまま流用すると、何も測らないまま通る。**
この worktree で実測した 2 つの落とし穴:

| 壊し方 | int8 blob（654,032 B）で何が起きたか |
|---|---|
| `dd ... bs=1 seek=1200000`（fp32 用の位置） | **ファイル末尾より後ろ**なので dd が 1,200,004 B に**伸ばすだけ**。重みは無傷 → **PASS** |
| offset 400,000 に 4 バイト | blob v2 の **`align16(cin)` の 0 埋め**に当たると出力が 1 bit も動かない → **PASS** |

いま入れてあるのは **「ファイルの真ん中 64 KB を `0x7f` で塗りつぶす」+「大きさが変わっていないことの検査」**。
fp32 / int8 の両方で落ちることを実測した（伸ばすだけの壊し方は大きさの検査で止まる）。

## ⚠️ `check_ci_coverage.py` は `ci.yml` しか読まない

`pages.yml`（Pages への配置）に書いたゲートは**誰も監査しない**。
wasm のゲートを `ci.yml` の `web` job に置いているのはそのため。
**Pages のワークフローに受け入れゲートを足さないこと** — 足すなら `ci.yml` 側にも同じものを置く。

## ⚠️ `web` job が緑でも言えないこと

- **ブラウザで動く**とは言えない。ゲートは **node** で走る（emsdk 同梱の node）。**node はブラウザではない**（C-055）。
  ⚠️ **ブラウザは別途測ってある**（M-95 Chrome 152 / M-96 聴取）が、**それはゲートではない**（人と手作業が要る）
- **音が正しい**とは言えない。⚠️ 聴取は **M-96**（両レーンとも「問題なかった」/ 途切れ無し。
  **1 名・対照なし・盲検なし**）。⚠️ **実機の G32（対照つき）は別物で、まだ残っている**
- **漢字経路が合っている**とは言えない。⚠️ 2 つに分かれる:
  - **G-W6 の「漢字 == かな」** … 辞書 13.7 MB が CI に無いので回らない
    （**skip せず「回せなかった」と理由つきで出る**）。手元では回る
  - **ホストとの全段一致**（K-7 の G25 相当） … `kanji_e2e_vectors.bin`（19 MB）が
    git にもリリースにも無い。⚠️ **番号は振っていない**（未実装）
