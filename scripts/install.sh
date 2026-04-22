#!/usr/bin/env bash
set -euo pipefail

# korean-jangbu-for 설치 스크립트
# 1. poppler (PDF→이미지) 설치 확인
# 2. MCP 서버 가상환경 + 의존성
# 3. OS별 OCR 엔진 (macOS→Vision, Linux/Win→PaddleOCR)
# 4. SQLite 스키마 + 시드 주입
# 5. Claude Code 스킬 심볼릭 링크

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SKILLS=(korean-jangbu-for jangbu-connect jangbu-import jangbu-tag jangbu-tax jangbu-dash jangbu-jongso)

echo "━━━ korean-jangbu-for 설치 ━━━"
echo ""

# 0. poppler
echo "[0/5] poppler 확인"
if ! command -v pdftoppm >/dev/null; then
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "  → brew install poppler"
        brew install poppler
    else
        echo "  ❌ poppler 설치 필요: apt install poppler-utils (Debian) / yum install poppler-utils (RHEL)"
        exit 1
    fi
fi
echo "  ✅"

# 1. MCP 서버 가상환경
echo "[1/5] Python 가상환경"
cd "$ROOT/mcp-server"
python3 -m venv .venv
source .venv/bin/activate
pip install --quiet --upgrade pip

# 2. 기본 의존성
echo "[2/5] 기본 의존성 설치"
pip install --quiet -e .

# 3. OS별 OCR
echo "[3/5] OCR 엔진"
if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "  macOS 감지 → Vision Framework 사용 (추가 다운로드 없음)"
    pip install --quiet -e ".[macos]"
else
    echo "  PaddleOCR 설치 (약 500MB)"
    pip install --quiet -e ".[paddle]"
fi

# HEIC/HEIF 지원 (iOS 사진 포맷)
echo "  HEIC/HEIF 지원 설치"
pip install --quiet -e ".[heif]" || echo "  ⚠ pillow-heif 설치 실패 — HEIC 지원 없이 진행"

# 4. SQLite + 시드
echo "[4/5] SQLite 초기화 + 시드 주입"
cd "$ROOT"
PYTHONPATH="$ROOT/mcp-server" python -m scripts.seed

# 5. 스킬 링크
echo "[5/5] Claude Code 스킬 등록"
mkdir -p ~/.claude/skills
for s in "${SKILLS[@]}"; do
  ln -sfn "$ROOT/skills/$s" "$HOME/.claude/skills/$s"
  echo "  → $s"
done

echo ""
echo "━━━ 설치 완료 ━━━"
cat <<EOF

다음 명령으로 MCP 서버 등록:

  claude mcp add jangbu-mcp -- $ROOT/mcp-server/.venv/bin/python -m jangbu_mcp

검증:

  bash $ROOT/scripts/verify.sh

Claude Code에서 /korean-jangbu-for 로 시작.
EOF
