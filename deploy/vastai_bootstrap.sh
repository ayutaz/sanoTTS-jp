#!/usr/bin/env bash
# vast.ai インスタンスの立ち上げからラベル生成・学習まで。
#
#   bash deploy/vastai_bootstrap.sh setup      # uv / piper-plus / 依存 / ckpt
#   bash deploy/vastai_bootstrap.sh parity     # ★ D-015 のゲート。ここを通さず先に進まない
#   bash deploy/vastai_bootstrap.sh labels     # 本番ラベル生成（一度だけ）
#   bash deploy/vastai_bootstrap.sh train      # 生徒の学習
#
# 前提: このリポジトリを丸ごと転送してあること（テキスト 1.2 MB + スクリプト）。
#       ckpt 927 MB は HF から直接引くので**アップロードしない**。
#       HF_TOKEN を環境変数で渡すこと（教師 repo は private）。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PIPER_PLUS_ROOT="${PIPER_PLUS_ROOT:-$HOME/piper-plus}"
PIN_COMMIT="0f3b1a62fa3b9a323c92ad709288fd80b42ff18f"
PIN_REPO="https://github.com/ayutaz/piper-plus.git"
cd "$REPO"

step() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
die()  { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

setup() {
  step "uv"
  command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  uv --version

  step "piper-plus を $PIN_COMMIT で clone"
  if [ ! -d "$PIPER_PLUS_ROOT/.git" ]; then
    git clone --filter=blob:none "$PIN_REPO" "$PIPER_PLUS_ROOT"
  fi
  git -C "$PIPER_PLUS_ROOT" fetch origin "$PIN_COMMIT"
  git -C "$PIPER_PLUS_ROOT" checkout --detach "$PIN_COMMIT"

  step "pyproject.toml の path 依存をこのインスタンスのパスに向ける"
  python3 deploy/retarget_sources.py --root "$PIPER_PLUS_ROOT"

  step "依存を同期"
  uv sync --extra eval

  step "教師ソースの同一性を照合（ここが違うとラベルが別物になる）"
  uv run python -c "
import sys; sys.path.insert(0,'src')
from saanotts_jp.teacher_identity import verify, piper_plus_root
print('root =', piper_plus_root()); verify(); print('✅ 教師ソースはピン留めと一致')"

  step "教師 ckpt を HF から取得（927 MB / private repo）"
  [ -n "${HF_TOKEN:-}" ] || die "HF_TOKEN が未設定。private repo なので必須"
  uv run python -c "
from huggingface_hub import snapshot_download
p = snapshot_download('ayousanz/piper-plus-zero-shot-tsukuyomi',
                      allow_patterns=['epoch=499-step=22000.ckpt','config.json','eval/*'])
print('ckpt:', p)"

  step "セットアップの健全性チェック"
  uv run python scripts/phase0_verify_teacher.py
}

parity() {
  step "★ D-015 ゲート: CPU と CUDA でラベルが一致するかを実測"
  uv run python scripts/b4_device_parity.py --device cuda \
    --out reports/b4_device_parity_cuda.json
  cat <<'EOF'

reports/b4_device_parity_cuda.json を**必ず読んでから**次に進むこと。
CPU と GPU が bit 一致しないのは想定内（ローカル MPS で SNR 97〜106 dB だった）。
判断材料は「int16 量子化後に何サンプルずれるか」。パックの int16 自体が 76.9 dB なので、
それより十分良ければ GPU 生成でよい。TF32 が効いていると差が広がるので、
不安なら `TORCH_ALLOW_TF32_CUBLAS_OVERRIDE=0` を付けて測り直す。
EOF
}

labels() {
  step "本番ラベル生成（**一度だけ**。D-015）"
  [ -d data/pack ] && die "data/pack が既にある。再生成は D-015 違反。意図して消すこと"
  uv run python scripts/gen_teacher_labels.py --split train   --out data/pack
  uv run python scripts/gen_teacher_labels.py --split heldout --out data/pack_heldout
  step "SHA-256 で固定"
  for d in data/pack data/pack_heldout; do
    ( cd "$d" && sha256sum -c SHA256SUMS ) || die "$d のハッシュが合わない"
  done
  du -sh data/pack data/pack_heldout
}

train() {
  step "生徒の学習"
  : "${STAGES:=1 2 3 4}"
  for s in $STAGES; do
    uv run python scripts/train_student.py --pack data/pack --stage "$s" \
      --steps "${STEPS:-20000}" --device cuda
  done
}

case "${1:-}" in
  setup)  setup ;;
  parity) parity ;;
  labels) labels ;;
  train)  train ;;
  *) die "usage: $0 {setup|parity|labels|train}" ;;
esac
