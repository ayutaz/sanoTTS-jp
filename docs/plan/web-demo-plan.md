# W トラック — ブラウザで動くランタイムデモ（GitHub Pages）

**目的**: C99 推論コアと漢字経路をそのまま WebAssembly にして、
`https://ayutaz.github.io/sanoTTS-jp/` で **漢字かな交じり文を打つと喋る**最小デモを配る。

⚠️ **これは D-007（ブラウザは対象外）を覆すものではない。**
成果物は今も ESP32 の 567 K で、Web は**触れる入口**でしかない。
位置づけは **D-050** に記録する。

⚠️ **上流 `Ampixa/sanoTTS`（GPL-3.0）も WASM デモを配っている**（`docs/upstream-sanotts.md`）。
やること自体は事実であって著作物ではないが、**実装を見てはいけない**（D-032）。
hook が `git clone` / `gh api .../contents/*.js` を deny する。**このデモは clean-room のまま。**

---

## 0. なぜ成立すると言えるのか（着手前の実測）

計画を書く前に**動くところまで確かめた**。以下は全部この worktree での自己実測。

| 何を | 結果 |
|---|---|
| コアの外部シンボル | `cosf` / `expf` / `memcpy` / `memmove` / `bzero` / `snprintf` / `vsnprintf` / `strncmp` だけ |
| 漢字経路の移植性 | `esp32/main/saan_kanji.c` は **ESP-IDF の include が 0 本**。そのまま使える |
| 全経路の通し（wasm） | 漢字文 → PCM が完走し、**同じ文をかなで書いた PCM と bit 一致** |
| K-7 G25（wasm） | ホストと **298/298 一致**（陰性対照 298 件が食い違う） |
| 品質（wasm W8A32） | held-out 24 文で fp32 比 **平均 28.11 dB / 最小 25.72 dB** — 受け入れゲート通過 |
| 速度（wasm / node） | 5 レーンが **0.008〜0.033 ×RT**（最遅は W8A32 の 0.033）。⚠️ **node であってブラウザではない** |

詳細と再現コマンドは **M-94**（`docs/measurements.md`）。

---

## 1. 動作点（凍結する）

| | 値 | なぜ |
|---|---|---|
| レーン | **W8A32 と W8A8 の 2 本** | W8A32 は品質ゲートを通り、W8A8 は**実機の出荷構成そのもの**。blob は共通なので wasm が 1 本増えるだけ |
| SIMD | **`-msimd128` を有効** | held-out 24 文で **PCM が bit 一致**したうえで速くなる（速度だけの選択）。W8A8 で **23.79 → 10.23 ms**（M-94 §1。node / n=25 の min）。⚠️ **比は書かない** — 負荷が変動する機械の壁時計なので、M-94 が 10% の精度での主張を否定している |
| blob | **int8（`saanotts-jp-v3-int8.bin` / 654,032 B）** | ① **fp32 blob の 1/3.4**（2,249,792 → 654,032 B。M-94 §8）② **W8A8 レーンは int8 blob でしか成立しない**（fp32 blob を渡しても例外は出ず、黙って W8A32 と同じ出力になるだけ。§5-8 / G-W5）③ 実行時に dtype で分岐するので後から差し替えられる。⚠️ **速度は理由にならない** — かつて書いた「fp32 と速度が同じ」は**M-94 §1b で取り下げた**（実測は W8A32 40.41 ms > fp32 36.01 ms で、W8A32 の方が遅い） |
| 辞書 | **`k1-dict-438750.bin` / 13,702,320 B** | ⚠️ **CI では再生成できない**（凍結 sys.dic 103 MB が git 管理外）。リリースから落とすしかない |
| arena | **180,224 B（ESP32 と同じ）** | ブラウザなら緩められるが、**同じ枠で動くことがそのまま「MCU に載る」の証拠**になる |
| emcc | **6.0.9 を固定** | `setup-emsdk` の `version` 既定は `latest`。固定しないと M-94 が再現しない |

⚠️ **一括版 `saan_synthesize` は使わない。** 事前確保が 53 ids で 39.9 MB / 350 ids で 263.4 MB になる。
ストリーミング版は固定 180,224 B で足り、**出力は bit 一致する**。

---

## 2. 構成

```
ブラウザ
  ├─ index.html / main.js   ← 入力欄・再生・進捗。**ロジックを持たない**
  └─ saan_web.wasm          ← 経路判定・G2P・辞書・合成を全部やる
        ↑ fetch
     student_i8.bin + k1_dict.bin.gz
```

**経路判定を JS に書かない。** `saan_g2p_classify()` が かな / 辞書 / 拒否 を決める規約で
（`csrc/g2p.h`）、ホストと端末の一致を `make -C csrc kb-parity` が守っている。
JS 側に「ひらがなっぽいから」を作った瞬間に入力仕様の目的が崩れる。**JS は生の文字列を渡すだけ。**

新規に書く C は **`web/saan_web.c` 1 本だけ**。`esp32/main/main.c` の `synth_once()` の順序を移す。
`esp32/main/saan_kanji.c` と `esp32/main/saan_pcm.c` は**そのままリンクする**。

---

## 3. 作業（W-0 〜 W-8）

| # | 何 | 受け入れゲート |
|---|---|---|
| **W-0** | `web/saan_web.c` — arena / blob / 経路判定 / 合成 | **G-W6**（出荷バイナリを実際に走らせる） |
| **W-1** | `web/build.sh` — emcc 6.0.9 で 2 レーン × SIMD | 2 本の `.wasm` と `.mjs` が出る |
| **W-2** | `web/index.html` + `web/main.js` — 最小 UI | 手元プレビューで鳴る |
| **W-3** | ライセンス表示 | **G-W7**（LICENSE-MODEL.md §3.1 の 22 行と一字一句一致） |
| **W-4** | `.github/workflows/pages.yml` | ✅ **マージ前に実ブランチで build job を回して緑**（run 33828197417。`upload-pages-artifact` まで通り artifact **6,237,222 B**）。⚠️ **`deploy` だけは未実行** — `github-pages` 環境の branch policy が `main` の 1 件だけなので、`if: github.ref == 'refs/heads/main'` で skip させた |
| **W-5** | `scripts/check_web_gates.sh` — **G-W1 / G-W2 / G-W2b / G-W3 / G-W4 / G-W5 / G-W6 / G-W7** | 陽性対照が落ちる |
| **W-6** | `ci.yml` に web job と **int8 レーン**を追加 | C-057 とセット |
| **W-7** | ドキュメント（D-050 / M-94 / C-057 / README / CLAUDE.md） | `check_doc_counters.py` / `check_doc_links.py` |
| ~~**W-8**~~ | ~~Pages の有効化~~ → ✅ **2026-09-04 に有効化された** | ⚠️ **手作業だった**（`configure-pages` の `enablement: true` は既定トークンでは効かない）。⚠️ URL が開くのは **`main` にマージされてから**（`pages.yml` は push: main だけ） |

---

## 4. 受け入れゲート

### CI で回せる（リリースの資産だけで足りる）

実装は `scripts/check_web_gates.sh`（節番号はスクリプトの `hdr` と同じ）。

| | 何を見るか | 陽性対照 |
|---|---|---|
| **G-W7** | `web/index.html` の帰属ブロックが `LICENSE-MODEL.md` §3.1 と**一字一句一致** | 1 行消すと落ちる。⚠️ **行番号で切らない**（§3.1 の上に 1 行入ると黙ってずれる） |
| **G-W1** | wasm(fp32) が `golden-v3-fp32.bin` と一致 | 重みの真ん中 64 KB を塗ると落ちる |
| **G-W2** | wasm(int8 / W8A32) が `golden-v3-int8.bin` と一致 | 同上 |
| **G-W2b** | **ブラウザが通るストリーミング経路**が一括版と bit 一致（`stream_test` を 3 レーン） | — |
| **G-W3** | `-msimd128` の有無で PCM が **bit 一致** | 1 サンプルずらすと落ちる |
| **G-W4** | 経路判定（`saan_g2p_classify`）が凍結ベクタと一致 | 手書き文字集合版が落ちる |
| **G-W5** | fp32 blob を W8A8 レーンに渡すと**拒否される** | 検査を外すと黙って通る |
| **G-W6** | **`web/saan_web.c` を実際にビルドし、出荷する `web/dist/*.mjs` を叩く** | `#error` / `saan_pcm_reset()` を消すと落ちる |

⚠️ **G-W1 の陽性対照を `ci.yml` の旧版からコピペしない。**
`dd seek=1200000` は 654,032 B の blob では**ファイルを伸ばすだけで PASS する**。
1 バイト・4 バイトの書き換えも、blob v2 の 0 埋めに当たると PASS する（実測）。
だから**真ん中 64 KB を塗り、サイズが変わっていないことも見る**。

⚠️ **G-W2b が要る理由**: `golden_test.c` は**一括版** `saan_synthesize` を呼ぶ。
`web/saan_web.c` は `saan_stream_init` / `saan_stream_pull` を使う。
G-W1/G-W2 が緑でも、**ブラウザが通る経路は 1 行も検査されていなかった**。

⚠️ **G-W6 が要る理由**: これを入れるまで `web/saan_web.c` は
**どのゲートでも 1 度もコンパイルされていなかった**（`#error` を入れても exit 0）。
出荷バイナリを `instantiateWasm` で読むので、node 向けに組み直した「別のもの」ではない。

### CI で回せない（理由を残す）

| | なぜ |
|---|---|
| **G-W6 の「漢字 == かな」と「ids 350 超えの拒否」** | 辞書 13.7 MB が CI に無い。⚠️ **skip せず「回せなかった」と理由つきで出る** |
| **G-W2b の多文レーン** | `ids_heldout.bin` がコーパス由来。CI で回るのは golden の 1 文だけ |
| 漢字経路の**全段一致**（K-7 の G25 相当） | `kanji_e2e_vectors.bin` 19 MB が git にもリリースにも無い。⚠️ **番号は振っていない**（未実装） |
| held-out 24 文の SNR | `ids_heldout.bin` がコーパス由来 |
| **`web/main.js`** | どのゲートも 1 行も走らせていない。⚠️ **`saan_web_message()` の表示を消しても全ゲートは緑** |
| **ブラウザでの実測** | ⚠️ **ブラウザは 1 種類も測っていない。node の値はブラウザの値ではない**（C-055） |
| **聴取** | ⚠️ **ブラウザの音は誰も聴いていない** |

---

## 5. ⚠️ 踏むと黙って壊れるもの

実測で確かめた順に並べる。**どれも例外が出ない。**

1. **emcc の `malloc()` は 8 バイト境界しか保証しない**（実測で `mod16 == 8`）。
   辞書を素の `_malloc` に置くと `jdict` の `matrix` が `const int16_t*` として 8 境界から読まれる。
   **wasm はアラインメント例外を出さないので手元では動く。** `aligned_alloc(16, n)` を使い、
   `esp32/main/saan_dict.c` と同じ 16 境界検査を web 側にも置く。
2. **メモリ拡張で `Module.HEAPF32` が detach する**（実測で `byteLength` が 0）。
   pull のたびに読み直す。
3. **`saan_kanji_to_ids()` は 350 ids を強制しない。** 109 文字で `n_ids = 582` を OK で返し、
   次の `saan_stream_init` が `SAAN_ERR_ARENA` で落ちる。`main.c` と同じ順序で拒否する。
4. **`a.used` の二重防御はポインタ幅に依存する。** ホストで測った定数を写すと必ず落ちる。
   必ず `saan_stream_arena_used(n_ids)` を**その発話の n_ids で**呼ぶ。
5. **漢字の作業領域と合成の arena は同じメモリ。** 先に G2P、そのあと `saan_arena_init` で巻き戻す。
   逆にすると合成中に上書きされて**それらしい音**が出る。
6. **`saan_pcm.c` を書き直さない。** ヘッダが「ここが唯一の実装」と明記している。
7. **発話ごとに `saan_pcm_reset()`。** 呼ばないと 2 発話目の checksum が「1 + 2 発話目」になる。
8. **fp32 blob を W8A8 レーンに渡しても黙って動く**（W8A32 と同じ出力になるだけ）。
   `main.c` と同じ `duration.blocks.0.c1.weight.scale` の検査を入れる。
9. **`CHARSET_UTF_8` が無いと openjtalk が `#error` で止まる。** `SAAN_KANJI` だけでは足りない。
10. **`-std=c99` は `__STRICT_ANSI__` で `clock_gettime` を隠す。** `gnu17` を使う。
11. **`-DK7_EXTERNAL_SCRATCH=1` は効かないマクロ**（実体は `LABEL_IDS_EXTERNAL_SCRATCH`）。写さない。
12. **wasm の checksum を ESP32 の checksum と比べない。**
    一致するのは `|max|` と `Σx²` の相対差だけ。
13. **Pages は HTTP ヘッダを設定できない。** COOP/COEP が張れないので
    `-pthread` / `SharedArrayBuffer` は**構造的に使えない**。
14. **`.bin` が Pages で gzip されるかは未確認。** だから `.gz` を置いて
    `DecompressionStream('gzip')` で展開する。⚠️「たぶん圧縮される」と書かない。
15. **`upload-pages-artifact` はドット始まりを全部除外し、シンボリックリンクを許さない。**
16. **`python3 -m http.server` は hook が deny する。** `uv run python -m http.server` を使う。
17. **`check_ci_coverage.py` は `ci.yml` しか読まない。** `pages.yml` に書いたゲートは誰も監査しない。
    しかも **ci.yml のコメントに書いただけで「CI で回っている」判定になっていた**
    （2026-09-04 に `strip_comments()` で塞いだ）。
18. ⚠️ **辞書経路は「入力から音素が 1 つも作れなかった」を自分では言わない。**
    `saan_kanji_to_ids()` は `SAAN_KANJI_OK` を返し、`n_ids` が `{^,_,$}` の 3 個になる。
    そのまま合成すると **0.116 秒の無音**（`|max|` は正常発話の約 1/60 = −36 dB）が出て、
    **rc も 0・メッセージも空**。`Hello` `test` `2026` `。` が全部これ。
    → `n_ids <= 3` を見て**呼び出し側が警告を出す**（`web/saan_web.c`）。
    ⚠️ かな経路の `n_dropped_long` / `n_dropped_devoice` と同じ規約に揃えること。
19. ⚠️ **入力に NUL が混じると `nbytes` と `strlen` で判定が割れる。**
    下流の `question_type()`（`csrc/label_ids.c`）が `strlen` を見るので、
    凍結 ABI の `(ptr, nbytes)` をそのまま渡すと EOS が `?.` と `?` に分かれ、
    **サンプル数が変わる**（実測 25,600 / 22,784）。NUL 終端が無ければ**後続ヒープを読む**。
    → NUL を検出したら拒否し、**必ず NUL 終端した写しを下流へ渡す**。

---

## 6. ドキュメント

⚠️ **採番は M-94 / D-050 / C-057。D-049 は使わない**（RTF の分母用に予約され、9 ファイル 14 箇所から前方参照）。

| | 中身 |
|---|---|
| **M-94** | wasm の実測（**5 レーン**の速度 / SNR / SIMD の bit 一致 / 漢字 298/298 / 圧縮サイズ）。⚠️ 速度は §1b で 2 つ取り下げてある |
| **D-050** | ブラウザデモを配る決定。**D-007 を消さずに追記する** |
| **C-057** | 「リリースの int8 blob は v1」が誤りだった訂正 |

⚠️ 番号を足したら `docs/README.md:21,22,397,398` / `CONTRIBUTING.md:47,164` /
`.claude/skills/recording-measurements/SKILL.md:12` を直す。
⚠️ 見出しの直前に `<a id="m-94"></a>` を置く（どのゲートも見ていない）。
⚠️ **「CI は 4 job」は既に嘘だった**（W トラック着手時点で 5、web job を足して **6**）。
`CLAUDE.md` / `docs/README.md` / `.github/workflows/README.md` の 3 か所とも **6 job に直した**。
⚠️ **この数を検査するゲートは無い**ので、job を増減したら手で直すこと。

---

## 7. ライセンス

Pages に置くのは **重み + 辞書 + Open JTalk + Emscripten ランタイム**の 4 者の再配布。

| 対象 | 担当 | 何をする |
|---|---|---|
| 重み | `LICENSE-MODEL.md` | §3.1 の **22 行（:68-89）をそのまま**貼る。§3.2 の 4 禁止をページに出す |
| Open JTalk | `csrc/openjtalk/COPYING` | 修正 BSD の binary-form 条項。ページか同梱 NOTICE で満たす |
| 辞書 | ⚠️ **リポジトリに担当ファイルが無い** | NAIST / UniDic / 名工大の 3 権利者。リリースの `NOTICE-dictionary.txt` を同梱する |
| Emscripten | MIT/NCSA | ⚠️ emcc は生成物に**ライセンスを 1 文字も書かない**（実測） |

⚠️ **`NOTICE.md` 版の帰属ブロックを写さない** — §3.1 の 22 行と比べて **2 行足りない**。
見た目がほぼ同じで目視では気づけない。貼るのは `LICENSE-MODEL.md:68-89`。

⚠️ **§3.4(c) を踏みやすい。** 上流も WASM デモを配っているので、ページ本文に
**「arXiv:2608.21378 の独立再実装であり著者らの実装ではない」**を明記する。
キャラクター名を大きく出すと §3.4(b)（推奨・承認の示唆）も踏む。

⚠️ **生成音声のダウンロード機能は付けない。** §3.2 の「素材としての再配布」の線引きを曖昧にしない。

---

## 8. 残る未検証

- **ブラウザで 1 種類も測っていない。** node の値はブラウザの値ではない（C-055）
- **モバイルで測っていない**
- **ブラウザの音を誰も聴いていない**（G32 と同じ空白）
- `AudioContext` は 22,050 Hz を直接は保証しない。**リサンプルが挟まるので出音は checksum と一致しない**
- `.bin` に対する Pages の `Content-Encoding` は未確認（だから `.gz` を置く）
