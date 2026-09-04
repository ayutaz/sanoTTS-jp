/* sanoTTS-jp — ブラウザデモの UI 側。
 *
 * ここが持つのは「入力を wasm に渡して、返ってきた PCM を鳴らす」だけ。
 *
 * ⚠️ **経路判定（かな / 辞書 / 拒否）を JS に書かないこと。**
 *    決めるのは wasm の `saan_g2p_classify()` で、ホストと端末の一致は
 *    `make -C csrc kb-parity` が守っている。JS 側に「ひらがなだから」を
 *    1 行でも作った瞬間に「ホストと端末で同じ列」という入力仕様の目的が崩れる。
 *    JS は**生の文字列をそのまま渡すだけ**。
 *
 * ⚠️ 正規化もしないこと（端末は `。` すら拒否する。CLAUDE.md「入力仕様」）。
 *    trim すら足すと、端末だけの規則が 1 つ増える。
 */
'use strict';

/* ─────────────────────────────────────────────────────────────────────────
 * 配信物の名前
 *
 * ⚠️ **index.html と同じ階層に全部が平らに並ぶ前提**（Pages に上げるディレクトリの中身）:
 *
 *      index.html
 *      main.js
 *      saan_web_w8a32.mjs / .wasm    ← web/build.sh は web/dist/ に出す。**そこから移す**
 *      saan_web_w8a8.mjs  / .wasm
 *      student_i8.bin                ← リリースの saanotts-jp-v3-int8.bin（654,032 B）
 *      k1_dict.bin.gz                ← リリースの k1-dict-438750.bin（13,702,320 B）を gzip したもの
 *      NOTICE.txt / NOTICE-openjtalk.txt / NOTICE-dictionary.txt
 *
 * ⚠️ **ここが実際に置いた名前と食い違うと 404 になる。**
 *    名前や階層を変えるならこの表だけ直す（他の場所にパスを書かない）。
 * ⚠️ `.gz` で終わる URL は `DecompressionStream('gzip')` を通す。
 *    Pages は `.bin`（`application/octet-stream`）にも **gzip を掛ける**（M-94 §12 で実測）。
 *    ⚠️ **それでも辞書だけは自前で `.gz` を置く。** 配信側の方針は予告なく変わりうるので、
 *    転送量を server の設定に依存させない。
 * ───────────────────────────────────────────────────────────────────────── */
const ASSETS = {
  lanes: {
    /* blob は 2 レーンで共通。違うのは wasm の方（活性が float32 か int8 か）。 */
    w8a32: { mjs: './saan_web_w8a32.mjs', label: 'W8A32' },
    w8a8:  { mjs: './saan_web_w8a8.mjs',  label: 'W8A8'  },
  },
  model: './student_i8.bin',      /* int8 blob v2 */
  dict:  './k1_dict.bin.gz',      /* 辞書。展開後 13,702,320 B */
};

/* `saan_web_lane()` の戻り（0 = W8A32 / 1 = W8A8）。 */
const LANE_NAME = { 0: 'W8A32', 1: 'W8A8' };

const PRESETS = [
  '今日は良い天気ですね。',
  /* 上と同じ文のかな中間表現（= かな経路）。
   * **漢字経路とこの経路で PCM が bit 一致することを、node と Chrome の両方で確かめてある**
   * （M-94 §6 / M-95 §1。⚠️ Chrome では **ubuntu の CI が焼いた wasm** で確かめた）。 */
  'きょ][おわよ][いて][んきです°ね',
  '音声合成をマイコンの上で走らせます。',
  '明日の天気は晴れのち曇りでしょう。',
];

/* ───────────────────────────── DOM ───────────────────────────── */
const $ = (id) => document.getElementById(id);
const el = {
  text:   $('text'),
  play:   $('play'),
  presets: $('presets'),
  barWrap: $('bar-wrap'),
  bar:    $('bar'),
  status: $('status'),
  error:  $('error'),
  result: $('result'),
  lane:   $('r-lane'),
  route:  $('r-route'),
  ids:    $('r-ids'),
  ms:     $('r-ms'),
  dur:    $('r-dur'),
  arena:  $('r-arena'),
  msg:    $('r-msg'),
};

/* ───────────────────────────── 状態 ───────────────────────────── */
const state = {
  lane: 'w8a32',
  mod: null,          /* 読み込み済み Module（1 レーンぶんだけ持つ） */
  modLane: null,      /* mod がどのレーンのものか */
  bytes: {},          /* fetch 済みの生バイト（レーンを切り替えても再取得しない） */
  textBuf: 0,         /* 入力文字列を書く wasm 側バッファ */
  textCap: 0,
  ctx: null,          /* AudioContext */
  src: null,          /* いま鳴っている AudioBufferSourceNode（⚠️ 下の stopPlaying） */
  busy: false,
};

/* ───────────────────────── 小さい道具 ───────────────────────── */

const fmt = (n) => n.toLocaleString('en-US');

function setStatus(s) { el.status.textContent = s; }

/* C 側のメッセージを表示する。
 *
 * ⚠️ **`**強調**` は消さずに太字にする。** これはこのプロジェクトの実行時メッセージの規約で、
 *    ESP32 の `esp32/main/main.c` も `ESP_LOGE` で同じ書き方をしている（16 箇所）。
 *    `textContent` にそのまま入れると `**` が文字として見えてしまう。
 * ⚠️ **必ず先に HTML をエスケープする。** 入力文字列の一部（拒否された文字）が
 *    メッセージに載るので、エスケープを飛ばすと入力が HTML として解釈される。 */
function setMarkedText(node, s) {
  const esc = String(s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
  node.innerHTML = esc.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
}

function showError(s) {
  setMarkedText(el.error, s);
  el.error.hidden = false;
}
function clearError() {
  el.error.textContent = '';
  el.error.hidden = true;
}

function setProgress(frac) {
  if (frac === null) {
    el.barWrap.hidden = true;
    return;
  }
  el.barWrap.hidden = false;
  el.bar.style.width = `${Math.max(0, Math.min(1, frac)) * 100}%`;
}

/* 結果表を「まだ何も出していない」状態に戻す。
 *
 * ⚠️ **結果表を残したまま早期 return する道を作らないこと。**
 *    表に残るのは**前の発話**の ids / 合成時間 / 音声長で、エラーは別の枠に出るので、
 *    「いま入れた文の数字」としてそのまま読めてしまう。例外も警告も出ない。
 *    実際に空文字入力でこれを踏んだ。 */
function resetResult() {
  el.lane.textContent  = '—';
  el.route.textContent = '—';
  el.ids.textContent   = '—';
  el.ms.textContent    = '—';
  el.dur.textContent   = '—';
  el.arena.textContent = '—';
  el.msg.textContent   = '';
  el.result.hidden = true;
}

/* wasm の `const char *` を JS 文字列に。
 * ⚠️ `Module.UTF8ToString` は EXPORTED_RUNTIME_METHODS に入っていないと undefined。
 *    入っていなくても動くよう、HEAPU8 から自前で読む道も用意しておく。 */
function cstr(mod, ptr) {
  if (!ptr) return '';
  if (typeof mod.UTF8ToString === 'function') return mod.UTF8ToString(ptr);
  const h = mod.HEAPU8;                 /* ⚠️ 毎回 Module から読み直す（下記の detach） */
  let end = ptr;
  while (h[end] !== 0) end++;
  return new TextDecoder('utf-8').decode(h.subarray(ptr, end));
}

/* ───────────────────────── ダウンロード ───────────────────────── */

/* 進捗つきで取ってくる。`.gz` なら展開して返す。
 * ⚠️ 進捗の分母は **圧縮後の Content-Length**（展開後の長さは事前に分からない）。
 * ⚠️ Content-Length が無い配信（chunked）もあるので、その場合は受信バイトだけ出す。 */
async function fetchBytes(url, onProgress) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} を取得できませんでした（HTTP ${res.status}）。`);

  const total = Number(res.headers.get('content-length')) || 0;
  let received = 0;

  let stream = res.body;
  if (stream) {
    stream = stream.pipeThrough(new TransformStream({
      transform(chunk, ctrl) {
        received += chunk.byteLength;
        onProgress(received, total);
        ctrl.enqueue(chunk);
      },
    }));
    if (url.endsWith('.gz')) {
      if (typeof DecompressionStream !== 'function') {
        throw new Error(
          'このブラウザは DecompressionStream に対応していないので、辞書を展開できません。\n' +
          '（Chrome 80+ / Safari 16.4+ / Firefox 113+ 相当が必要です）');
      }
      stream = stream.pipeThrough(new DecompressionStream('gzip'));
    }
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }

  /* body が読めない環境向けの退路。進捗は出せない。 */
  const buf = new Uint8Array(await res.arrayBuffer());
  onProgress(buf.length, buf.length);
  if (!url.endsWith('.gz')) return buf;
  if (typeof DecompressionStream !== 'function') {
    throw new Error('このブラウザは DecompressionStream に対応していないので、辞書を展開できません。');
  }
  const ds = new DecompressionStream('gzip');
  const w = ds.writable.getWriter();
  w.write(buf); w.close();
  return new Uint8Array(await new Response(ds.readable).arrayBuffer());
}

async function fetchOnce(key, url, label) {
  if (state.bytes[key]) return state.bytes[key];
  setStatus(`${label} を読み込み中…`);
  const b = await fetchBytes(url, (got, total) => {
    if (total > 0) {
      setProgress(got / total);
      setStatus(`${label} を読み込み中… ${(got / 1048576).toFixed(1)} / ${(total / 1048576).toFixed(1)} MB`);
    } else {
      setProgress(null);
      setStatus(`${label} を読み込み中… ${(got / 1048576).toFixed(1)} MB`);
    }
  });
  state.bytes[key] = b;
  return b;
}

/* ───────────────────────── wasm の用意 ───────────────────────── */

/* wasm 側に nbytes 確保して bytes を書く。
 * ⚠️ **`saan_web_alloc` は 16 B 境界を返す**（emcc の `malloc` は実測 6/6 で 8 B 境界しか返さず、
 *    jdict の matrix も arena も 16 B 前提。wasm は境界例外を出さないので黙って壊れる）。
 * ⚠️ **`Module.HEAPU8` は set の直前に読み直す。** wasm メモリが拡張されると
 *    それまでの TypedArray は detach し、実測で byteLength が 0 になる。 */
function pushBytes(mod, bytes) {
  const ptr = mod._saan_web_alloc(bytes.length);
  if (!ptr) throw new Error(`wasm 側で ${fmt(bytes.length)} B を確保できませんでした（メモリ不足）。`);
  mod.HEAPU8.set(bytes, ptr);
  return ptr;
}

async function ensureModule(lane) {
  if (state.mod && state.modLane === lane) return state.mod;

  const model = await fetchOnce('model', ASSETS.model, 'モデル');
  const dict  = await fetchOnce('dict',  ASSETS.dict,  '辞書');

  setProgress(null);
  setStatus(`${ASSETS.lanes[lane].label} を初期化中…`);

  /* ⚠️ 古い Module は捨てる（レーンごとに保持すると、辞書 13.7 MB を
   *    wasm メモリに 2 つ抱えることになる）。切り替えの再初期化は毎回走る。 */
  state.mod = null;
  state.modLane = null;
  state.textBuf = 0;
  state.textCap = 0;

  /* ⚠️ import に失敗したとき、素の "Failed to fetch dynamically imported module" だと
   *    何を置き忘れたのか分からない。URL を出す。 */
  let factory;
  try {
    factory = (await import(ASSETS.lanes[lane].mjs)).default;
  } catch (e) {
    throw new Error(
      `${ASSETS.lanes[lane].mjs} を読み込めませんでした（${String((e && e.message) || e)}）。\n` +
      'index.html と同じ階層に build.sh の出力（.mjs と .wasm）が置かれているか確認してください。');
  }
  const mod = await factory();

  const mPtr = pushBytes(mod, model);
  const dPtr = pushBytes(mod, dict);

  const rc = mod._saan_web_init(mPtr, model.length, dPtr, dict.length);
  if (rc < 0) throw new Error(cstr(mod, mod._saan_web_message()) || `saan_web_init が ${rc} を返しました。`);

  /* ⚠️ **選んだレーンと、実際に読まれた wasm が同じかを見る。**
   *    2 レーンは同じ blob を読むので、build.sh が 2 つの名前に同じバイナリを出しても
   *    どちらも普通に鳴ってしまい、UI だけ見ても気づけない。 */
  const want = (lane === 'w8a8') ? 1 : 0;
  const got  = mod._saan_web_lane();
  if (got !== want) {
    throw new Error(
      `${ASSETS.lanes[lane].mjs} は ${LANE_NAME[got] || `不明(${got})`} の wasm でした` +
      `（選んだのは ${ASSETS.lanes[lane].label}）。build.sh の出力名と ASSETS の表が食い違っています。`);
  }

  state.mod = mod;
  state.modLane = lane;
  return mod;
}

/* 入力文字列を wasm 側へ。
 * ⚠️ ABI に free が無いので、発話ごとに確保すると少しずつ漏れる。
 *    足りなくなったときだけ取り直して使い回す。 */
function writeText(mod, text) {
  const need = (typeof mod.lengthBytesUTF8 === 'function')
    ? mod.lengthBytesUTF8(text)
    : new TextEncoder().encode(text).length;

  if (!state.textBuf || state.textCap < need + 1) {
    state.textCap = Math.max(4096, need + 1);
    state.textBuf = mod._saan_web_alloc(state.textCap);
    if (!state.textBuf) throw new Error('入力文字列用のバッファを確保できませんでした。');
  }

  if (typeof mod.stringToUTF8 === 'function') {
    mod.stringToUTF8(text, state.textBuf, state.textCap);
  } else {
    const enc = new TextEncoder().encode(text);
    mod.HEAPU8.set(enc, state.textBuf);          /* ⚠️ HEAPU8 は毎回 Module から */
    mod.HEAPU8[state.textBuf + enc.length] = 0;
  }
  return { ptr: state.textBuf, len: need };
}

/* ───────────────────────── 再生 ───────────────────────── */

/* ⚠️ 22,050 Hz の AudioContext を作れるかはブラウザ次第。作れなければ既定の ctx に落ちるが、
 *    **どちらでもリサンプルが挟まりうる**ので「実機と同じ音」とは書かないこと。 */
function audioContext() {
  if (state.ctx) return state.ctx;
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) throw new Error('このブラウザは Web Audio API に対応していません。');
  try {
    state.ctx = new AC({ sampleRate: 22050 });
  } catch (e) {
    state.ctx = new AC();
  }
  return state.ctx;
}

/* 鳴っている音を止める。
 *
 * ⚠️ **次を鳴らす前に必ず呼ぶ。** `AudioBufferSourceNode` は `start()` した時点で
 *    自分で最後まで鳴るので、止めずにもう 1 本足すと**2 発話が重なって鳴る**。
 *    例外は出ず、結果表の数字も正しいままなので、**聴かないと気づけない**。
 * ⚠️ `stop()` が「もう終わった source」で throw するかは実装依存で、
 *    **そこは測っていない**（Chrome での実測 M-95 は wasm の凍結 ABI 直叩きで、
 *    UI の再生経路は 1 行も走らせていない）。
 *    ここで throw が抜けると onPlay の catch に落ちて**再生そのものが失敗扱い**になるので、
 *    握りつぶす。握りつぶしてよいのは「もう鳴っていない = やることが無い」からで、
 *    これは他の catch と違って情報を捨てていない。 */
function stopPlaying() {
  const src = state.src;
  if (!src) return;
  state.src = null;
  try { src.stop(); } catch (e) { /* もう終わっている。止める用は無い */ }
}

function play(ctx, pcm, sampleRate) {
  stopPlaying();                 /* ⚠️ 新しい source を作る前に。逆にすると下の onended が誤爆する */

  const buf = ctx.createBuffer(1, pcm.length, sampleRate);
  buf.getChannelData(0).set(pcm);
  const src = ctx.createBufferSource();
  src.buffer = buf;
  src.connect(ctx.destination);

  /* ⚠️ **`state.src === src` を確かめてから null にすること。**
   *    `stop()` を呼ぶと、止めた source の `onended` が**後から**飛んでくる。
   *    素朴に `state.src = null` と書くと**次の発話の source を消してしまい**、
   *    そのまた次の再生で前の音が止まらなくなる。
   *    = **1 回おきに重なる**という、いちばん気づきにくい壊れ方になる。 */
  src.onended = () => { if (state.src === src) state.src = null; };

  state.src = src;
  src.start();
}

/* ───────────────────────── 本体 ───────────────────────── */

async function onPlay() {
  if (state.busy) return;

  const text = el.text.value;   /* ⚠️ 加工しない。trim も正規化もしない（上のコメント参照） */

  /* 空文字だけは、数 MB を落としに行く前に止める。
   * ⚠️ これは**規則の二重実装ではない** — wasm 側も空入力を拒む（実測で rc = -7）。
   *    ここでやっているのはダウンロードを省くことだけで、経路も可否も決めていない。 */
  if (text.length === 0) {
    resetResult();               /* ⚠️ ここを飛ばすと前の発話の数字が残る（resetResult のコメント） */
    showError('文が空です。');
    return;
  }

  state.busy = true;
  el.play.disabled = true;
  clearError();
  resetResult();

  /* ⚠️ ユーザー操作の中で作って resume する（それより前は suspended のまま鳴らない）。
   *    await をまたぐと gesture 扱いが切れる環境があるので、いちばん先に取る。 */
  let ctx = null;
  try {
    ctx = audioContext();
    ctx.resume();
  } catch (e) {
    /* Web Audio が無くても合成結果は表示できるので、ここでは止めない */
  }

  try {
    const lane = state.lane;
    const mod = await ensureModule(lane);

    setProgress(null);
    setStatus('合成中…');

    const { ptr, len } = writeText(mod, text);

    const t0 = performance.now();
    const rc = mod._saan_web_synth(ptr, len);
    const ms = performance.now() - t0;

    const route = cstr(mod, mod._saan_web_route());
    const msg   = cstr(mod, mod._saan_web_message());

    const laneName = LANE_NAME[mod._saan_web_lane()] || '不明';

    if (rc < 0) {
      resetResult();
      el.lane.textContent = laneName;
      el.route.textContent = route || '—';
      el.result.hidden = false;
      showError(msg || `saan_web_synth が ${rc} を返しました。`);
      setStatus('');
      return;
    }

    const n  = mod._saan_web_n_samples();
    const sr = mod._saan_web_sample_rate();

    /* ⚠️ HEAPF32 は Module から読み直す（メモリ拡張で detach する）。
     *    slice() でコピーを取り、以後 wasm のメモリを見ない。 */
    const p = mod._saan_web_pcm();
    const pcm = mod.HEAPF32.slice(p >> 2, (p >> 2) + n);

    el.lane.textContent  = laneName;
    el.route.textContent = route;
    el.ids.textContent   = `${fmt(mod._saan_web_n_ids())} ids`;
    el.ms.textContent    = `${ms.toFixed(1)} ms`;
    el.dur.textContent   = `${(n / sr).toFixed(3)} 秒（${fmt(n)} sample / ${fmt(sr)} Hz）`;
    el.arena.textContent = `${fmt(mod._saan_web_arena_used())} B`;
    setMarkedText(el.msg, msg);   /* ⚠️ 成功時も警告が入ることがあるのでそのまま出す */
    el.result.hidden = false;

    if (ctx) {
      ctx.resume();
      play(ctx, pcm, sr);
      setStatus(`${ASSETS.lanes[lane].label} で合成しました。`);
    } else {
      setStatus('合成はできましたが、このブラウザでは再生できません。');
    }
  } catch (e) {
    showError(String((e && e.message) || e));
    setStatus('');
    setProgress(null);
  } finally {
    state.busy = false;
    el.play.disabled = false;
  }
}

/* ───────────────────────── 組み立て ───────────────────────── */

for (const s of PRESETS) {
  const b = document.createElement('button');
  b.type = 'button';
  b.textContent = s;
  b.addEventListener('click', () => { el.text.value = s; el.text.focus(); });
  el.presets.appendChild(b);
}
el.text.value = PRESETS[0];

for (const r of document.querySelectorAll('input[name="lane"]')) {
  r.addEventListener('change', () => {
    if (!r.checked) return;
    state.lane = r.value;
    /* ⚠️ blob は 2 レーン共通なので再ダウンロードは要らない。wasm を差し替えるだけ。 */
    setStatus(`レーンを ${ASSETS.lanes[r.value].label} にしました（次の再生で切り替わります）。`);
  });
}

el.play.addEventListener('click', onPlay);
