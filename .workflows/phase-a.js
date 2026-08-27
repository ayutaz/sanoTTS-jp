export const meta = {
  name: 'saanotts-jp-phase-a',
  description: 'Phase A: ラベル生成の入力経路と prosody の扱いを確定する（Phase B/C の前提）',
  phases: [
    { title: '調査', detail: 'A-1 経路統一 / A-2 prosody / A-3 ラベルパック設計を並列に' },
    { title: '検証', detail: '各結論を独立エージェントが反証' },
    { title: '決定', detail: 'Phase B の設計を確定して docs に書き出す' },
  ],
}

const REPO = '.'
const PP = '~/Documents/piper-plus'
const SCR =
  '<scratch>'
const BT = String.fromCharCode(96) // バッククォート。テンプレートリテラル内の escape 事故を避ける
const FENCE = BT + BT + BT
const q = (s) => BT + s + BT

const CONTEXT = [
  '# Phase A: ラベル生成の設計を確定する',
  '',
  REPO + ' は arXiv:2608.21378 "saanoTTS" の蒸留レシピを**日本語**に適用し、',
  '**ESP32 上で動く 567 K の TTS** を作るプロジェクト（検証 PoC・非配布）。',
  '',
  '**着手前に必ず読むこと（この順で）:**',
  '1. ' + REPO + '/CLAUDE.md — 運用ルール',
  '2. ' + REPO + '/docs/README.md — 現在地',
  '3. ' + REPO + '/docs/decisions.md — 確定事項 D-001〜D-013 と訂正 C-001〜C-012',
  '4. ' + REPO + '/docs/measurements.md — **数値の一次ソース** M-1〜M-16',
  '5. ' + REPO + '/docs/plan/phase0-1-implementation-plan.md の §0（ロードマップ）と §2（B-*）',
  '',
  '## 絶対に守るルール',
  '',
  '- **Python は ' + q('uv run python') + ' 経由。** ' + q('pip install') + ' は禁止（' + q('uv add') + ' を使う）。',
  '  PreToolUse hook が機械的に deny する',
  '- **piper-plus (' + PP + ') は読み取り専用。** checkout / commit / 編集の禁止。hook が deny する',
  '- **数値を書くときは実測して再現コマンドを添える。** 推測を数値として書かない（C-001〜C-012）',
  '- **教師を呼ぶときは ' + REPO + '/.claude/skills/teacher-inference/SKILL.md の 6 項目に従う**',
  '  （EMA の適用順序 / speaker_embeddings=None / lid=0 / 実 prosody / canonical な音素化 / stale piper_train）',
  '- 作業ファイルは ' + SCR + '/ に置く。成果物だけ ' + REPO + ' に書く',
  '',
  '## いま確定していること（再調査不要）',
  '',
  '- 教師 = ayousanz/piper-plus-zero-shot-tsukuyomi/epoch=499-step=22000.ckpt（HF キャッシュ済み）',
  '- 入力仕様 = **ひらがな + アクセント記号 [ ] # + 無声化マーク °**。',
  '  端末側は mora テーブル 951 B + ん の異音規則 18 件（scripts/kana_g2p.py、往復 100%）',
  '- コーパス = 23,271 行（train 20,946 / heldout 2,325 / embedded 183、data/splits/）',
  '- 教師品質 = UTMOS 1.748、実人間 2.305、比 0.758（M-10）',
  '- ESP32 メモリ = I2S 逐次出力で 96 KB。中止材料なし（M-16）',
  '- ラベルパック = 20,946 文で fp16+int16 **4.42 GB** / fp32 8.83 GB。教師推論は CPU 186 ms/文（M-15）',
  '',
  '## Phase A が決めること',
  '',
  '**いまラベル生成の経路が 2 つあり、噛み合っていない。**',
  '',
  FENCE,
  'デバイス:   中間表現 --[mora テーブル 951 B]--> 音素ID',
  'ラベル生成: 漢字文   --[MultilingualPhonemizer]--> 音素ID --> 教師',
  FENCE,
  '',
  '蒸留では**生徒が学ぶ入力と、デバイスが実際に作る入力が一致していなければならない。**',
  '',
  '## 出力ルール',
  '',
  '**推測と実測を厳密に区別する。** evidence には実行コマンドと出力、または file:line を書く。',
  '測れるものは測る。失敗した試みも報告する。',
  '',
].join('\n')

const FINDINGS = {
  type: 'object',
  additionalProperties: false,
  required: ['topic', 'summary', 'findings', 'recommendation', 'open_questions', 'artifacts'],
  properties: {
    topic: { type: 'string' },
    summary: { type: 'string', description: '3〜6文の日本語要約' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['claim', 'evidence', 'confidence'],
        properties: {
          claim: { type: 'string' },
          evidence: { type: 'string', description: '実行コマンドと出力 / file:line' },
          confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
        },
      },
    },
    recommendation: { type: 'string', description: 'Phase B でどう実装すべきかの具体案' },
    open_questions: { type: 'array', items: { type: 'string' } },
    artifacts: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['path', 'description'],
        properties: { path: { type: 'string' }, description: { type: 'string' } },
      },
    },
  },
}

const VERDICT = {
  type: 'object',
  additionalProperties: false,
  required: ['refuted', 'confirmed', 'missing', 'notes'],
  properties: {
    refuted: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['claim', 'why'],
        properties: { claim: { type: 'string' }, why: { type: 'string' } },
      },
    },
    confirmed: { type: 'array', items: { type: 'string' } },
    missing: { type: 'array', items: { type: 'string' } },
    notes: { type: 'string' },
  },
}

const TOPICS = [
  {
    key: 'path',
    title: 'A-1 ラベル生成の入力経路を中間表現に統一できるか',
    effort: 'high',
    prompt: [
      '## タスク: ラベル生成を中間表現から始められるか判定する',
      '',
      '目標の経路:',
      '',
      FENCE,
      '漢字文 --[ホスト・OpenJTalk]--> 中間表現 --[kana_g2p]--> 音素ID --> 教師 --> ラベル',
      '                                            ^ デバイスと同じ変換器',
      FENCE,
      '',
      '### 測ること',
      '',
      '1. **held-out 2,325 文 + embedded 183 文の全件**で、',
      '   (a) 漢字文 → text_to_phoneme_ids_and_prosody（現行の canonical 経路）と',
      '   (b) 漢字文 → 中間表現 → kana_g2p.intermediate_to_phonemes → 音素ID',
      '   を比較する。**音素ID列が一致するか、差分の規模はどれくらいか**',
      '',
      '   ⚠️ (a) は intersperse padding が入る（len(ids) はトークン数の約 2 倍 + 3）。',
      '   (b) と比較するときはこの差を正しく扱うこと。実装例が',
      '   scripts/phase0_verify_teacher.py と scripts/kana_g2p.py にある',
      '',
      '2. **B-1 が消えるかの判定。** 現行経路は MultilingualPhonemizer を通り、',
      '   かなを 1 文字も含まない行が丸ごと中国語音素になる（コーパスの 5.36%）。',
      '   中間表現経由なら JapanesePhonemizer しか通らないのでこの問題は消えるはず。',
      '   **実際に消えることを、該当する行を特定して確認せよ**',
      '',
      '3. **教師の出力まで一致するか。** 5 文では bit 完全一致を確認済み（M-14）。',
      '   **100 文以上に広げて、音声が bit 一致する割合を測る**',
      '',
      '4. 一致しない場合の内訳を分類する（無声化のみ / 読みが違う / 長さが違う / 記号の位置）',
      '',
      '### 判定して返すこと',
      '',
      '- ラベル生成の入力を中間表現に統一**できるか / できないか**',
      '- できるなら B-1 は**消えるか**',
      '- 表現できない 3.6%（アクセント記号がモーラ内部に来るケース、M-14）をどう扱うか',
      '  （除外する / 中間表現の文法を拡張する / 別扱い）',
      '',
      '**成果物**: ' + REPO + '/reports/a1_path_unification.json と測定スクリプト',
    ].join('\n'),
  },
  {
    key: 'prosody',
    title: 'A-2 prosody (A1/A2/A3) の扱いを決める',
    effort: 'high',
    prompt: [
      '## タスク: 中間表現が A1/A2/A3 を持たない問題をどう解決するか決める',
      '',
      '**問題**: 教師は prosody_features (A1/A2/A3) を受け取り、これは duration predictor に',
      'だけ入る（M-13 で確認済み。ピッチには影響しない）。一方、中間表現は A1/A2/A3 を持たない。',
      '',
      '**論文の生徒設計**: Dα は音素IDしか見ない。つまり生徒は',
      '「音素ID → 教師が prosody 込みで出した duration」を学ぶ。',
      '',
      '### 測ること',
      '',
      '1. **prosody の実効性を定量化する。** held-out 300 文以上で、',
      '   実 A1/A2/A3 / ゼロテンソル / None の 3 通りで dT を生成し比較。',
      '   - 総フレーム数の差の分布',
      '   - 音素ごとの duration の差の分布',
      '   - **アクセント記号が同じでも prosody で duration が変わる度合い**',
      '',
      '2. **生徒が学習可能かの判定。これが本題。**',
      '   **同じ音素ID列に対して、教師が異なる dT を返すケースがどれだけあるか。**',
      '   音素ID列が同じなら常に同じ dT なら生徒は決定的に学習できる。',
      '   異なる dT を返すなら、生徒はそれを予測できない（矛盾する教師信号）。',
      '',
      '   コーパス中で音素ID列が重複する文を探し、その dT のばらつきを測ること。',
      '   重複が少なければ、**人工的に prosody だけ変えて同じ音素ID列を作り**、',
      '   dT のばらつきを測る',
      '',
      '3. **ラベル生成で prosody をどう渡すべきか**を決める:',
      '   - (a) 実 A1/A2/A3 を渡す（教師品質は最良。生徒は phoneme ID から近似を学ぶ）',
      '   - (b) ゼロを渡す（教師と生徒の条件が揃うが、教師の duration 品質が落ちる）',
      '   - (c) 中間表現に A1/A2/A3 を含める（デバイスが持てないので却下だが検討はする）',
      '',
      '   **(a) と (b) で教師音声の品質（UTMOS）に差が出るかを実測して判断せよ。**',
      '   測り方は scripts/b5_measure_mos.py を参照。',
      '   ⚠️ UTMOS は日本語でスケールが圧縮されている（実人間 2.305）ので、',
      '   絶対値ではなく (a) と (b) の**差**を見ること',
      '',
      '### 判定して返すこと',
      '',
      '- ラベル生成時に prosody を渡すか渡さないか、その根拠',
      '- 生徒 duration net にアクセント記号以外の入力が必要か',
      '',
      '**成果物**: ' + REPO + '/reports/a2_prosody.json と測定スクリプト',
    ].join('\n'),
  },
  {
    key: 'pack',
    title: 'A-3 ラベルパックの形式を設計する',
    prompt: [
      '## タスク: Phase B で 20,946 文分のラベルを保存する形式を決める',
      '',
      '論文が要求するもの（式2/3/5/6/7 から逆算）:',
      '',
      '| ラベル | 用途 | サイズ (20,946 文) |',
      '|---|---|---:|',
      '| dT duration | 式2 の目標 | 8.1 MB |',
      '| zT 192ch 潜在 | 式3 の cT = Eρ(zT) の入力 | fp32 3.78 GB / fp16 1.89 GB |',
      '| yT 波形 | 式5 の目標 | fp32 5.04 GB / int16 2.52 GB |',
      '| **チャネルごとの μ_T, σ_T** | 式3 の N_T と式7 の σT_k | 小さい |',
      '',
      '### 決めること',
      '',
      '1. **格納形式。** npz / safetensors / 生バイナリ + index / HDF5 のどれか。',
      '   判断基準は (a) 学習時のランダムアクセス速度、(b) vast.ai のディスク、',
      '   (c) 部分再生成のしやすさ、(d) SHA-256 で固定できること',
      '2. **量子化。** zT は fp16 で十分か。**実際に fp16 に落として fp32 と比較し、',
      '   L_c の値がどれだけ変わるかを測れ**。yT の int16 も同様',
      '3. **波形を保存するか。** 式5 はマルチ解像度 STFT なので学習中に毎回 STFT を取る。',
      '   波形を持つ必要はあるが、**STFT を事前計算して持つ選択肢**もある。サイズを比較せよ',
      '4. **manifest の設計。** 生成環境（Python / torch / CUDA）、seed、フラグ、',
      '   各行の SHA-256、コーパスの由来。**GPU 推論が CPU と bit 一致するか未検証**（M-15）',
      '   なので、後から検証できる情報を残すこと',
      '5. **健全性ゲート。** 保存前に assert すべき項目',
      '   （ceil(dT) の総和 == zT のフレーム数 == 波形長/256、音素ID < 173、NaN、全ゼロ音声）',
      '',
      '### 判定して返すこと',
      '',
      '具体的なファイルレイアウトと、書き込み/読み出しのコード骨子。',
      '',
      '**成果物**: ' + REPO + '/reports/a3_pack_design.json と、形式ごとのサイズ・速度の実測',
    ].join('\n'),
  },
]

phase('調査')

const results = await pipeline(
  TOPICS,
  (t) =>
    agent(CONTEXT + t.prompt, {
      label: '調査:' + t.key,
      phase: '調査',
      schema: FINDINGS,
      effort: t.effort,
    }),
  (found, t) => {
    if (!found) return null
    const claims = (found.findings || [])
      .map((f, i) => i + 1 + '. [' + f.confidence + '] ' + f.claim + '\n   根拠: ' + f.evidence)
      .join('\n')
    const p = [
      '## タスク: 以下の結論を**反証**せよ',
      '',
      '別のエージェントが「' + t.title + '」を調べ、次の結論を出した。',
      'あなたの仕事は**これを信じることではなく、間違いを見つけること**。',
      '',
      '### 要約',
      found.summary,
      '',
      '### 主張',
      claims,
      '',
      '### 推奨',
      found.recommendation,
      '',
      '### やること',
      '1. 各 claim の根拠コマンドを**自分で実行して**確認する。存在しない file:line、',
      '   出力の誤読、再現しない数値があれば refuted に入れる',
      '2. **カバー率と精度が分離して報告されているか。** 「100%」が母数の絞り込みによる',
      '   見かけの数字でないか確認する（実際に踏んだ失敗、C-012 と同型）',
      '3. 「〜のはず」「おそらく」が実測として扱われていないか',
      '4. **held-out で測っているか。** 学習側のデータで測った数字は無意味',
      '5. 取りこぼした論点を missing に列挙する',
      '',
      '**疑わしければ refuted 側に倒すこと。**',
    ].join('\n')
    return agent(CONTEXT + p, {
      label: '検証:' + t.key,
      phase: '検証',
      schema: VERDICT,
    }).then((v) => ({ topic: t, found, verdict: v }))
  }
)

const ok = results.filter(Boolean)
log('調査 ' + ok.length + '/' + TOPICS.length + ' 件が検証まで完了。決定に進みます。')

phase('決定')

const digest = ok
  .map((r) => {
    const v = r.verdict || { confirmed: [], refuted: [], missing: [], notes: '(検証失敗)' }
    return [
      '====================================================================',
      '# ' + r.topic.title + ' (' + r.topic.key + ')',
      '',
      '## 要約',
      r.found.summary,
      '',
      '## 主張',
      (r.found.findings || [])
        .map((f) => '- [' + f.confidence + '] ' + f.claim + '\n  根拠: ' + f.evidence)
        .join('\n'),
      '',
      '## 推奨',
      r.found.recommendation,
      '',
      '## 生成物',
      (r.found.artifacts || []).map((a) => '- ' + a.path + ' — ' + a.description).join('\n') || '(なし)',
      '',
      '## 未解決',
      (r.found.open_questions || []).map((x) => '- ' + x).join('\n') || '(なし)',
      '',
      '## 検証: 確認された',
      (v.confirmed || []).map((c) => '- ' + c).join('\n') || '(なし)',
      '## 検証: 反証された',
      (v.refuted || []).map((c) => '- ' + c.claim + '\n  理由: ' + c.why).join('\n') || '(なし)',
      '## 検証: 取りこぼし',
      (v.missing || []).map((c) => '- ' + c).join('\n') || '(なし)',
      '## 検証者メモ',
      v.notes || '',
    ].join('\n')
  })
  .join('\n')

const OUT = REPO + '/docs/plan/phase-a-decisions.md'

const decisionPrompt = [
  '# 調査と反証検証の結果',
  '',
  digest,
  '',
  '====================================================================',
  '',
  '## タスク: Phase A の決定を書き、Phase B の実装仕様を確定する',
  '',
  OUT + ' に日本語の Markdown で書け。',
  '',
  '**方針:**',
  '- **反証されたものは採用しない。** refuted は載せないか「未確定」と明示',
  '- measured と estimated を明確に区別する',
  '- 数値には**どう測ったか**を添える',
  '- 「〜すべき」ではなく「**こうする**」と決め切る。Phase B が着手できる粒度まで落とす',
  '',
  '**構成:**',
  '1. **決定サマリ**（1 画面。A-1 / A-2 / A-3 それぞれ 1 行で）',
  '2. A-1: ラベル生成の入力経路 — 決定と根拠。B-1 は消えるか',
  '3. A-2: prosody の扱い — 決定と根拠',
  '4. A-3: ラベルパックの形式 — ファイルレイアウトと健全性ゲート',
  '5. **Phase B の実装仕様** — 何を作るか、完了条件は何か',
  '6. 未解決・要注意事項',
  '',
  '書き終えたら決定サマリを返り値に含めろ。',
].join('\n')

const decision = await agent(CONTEXT + decisionPrompt, {
  label: 'Phase A 決定',
  phase: '決定',
  effort: 'high',
})

return {
  out: OUT,
  topics: ok.map((r) => ({
    key: r.topic.key,
    confirmed: r.verdict && r.verdict.confirmed ? r.verdict.confirmed.length : 0,
    refuted: r.verdict && r.verdict.refuted ? r.verdict.refuted.length : 0,
    refuted_details: (r.verdict && r.verdict.refuted) || [],
    missing: (r.verdict && r.verdict.missing) || [],
    open_questions: r.found.open_questions || [],
  })),
  decision,
}
