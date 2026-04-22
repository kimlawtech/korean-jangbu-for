#!/usr/bin/env bash
# 설치 검증 스크립트
# 의존성·MCP·OCR·DB·룰 시드·단위테스트 전 구간 확인
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/mcp-server/.venv/bin/python"

echo "━━━ korean-jangbu-for 설치 검증 ━━━"

# 1. 가상환경
if [ ! -x "$PY" ]; then
    echo "❌ 가상환경 없음. scripts/install.sh 먼저 실행"
    exit 1
fi
echo "✅ 가상환경: $PY"

# 2. 의존성
"$PY" -c "import mcp, pandas, openpyxl, pdf2image; print('✅ 기본 의존성')" || exit 1

# 3. OCR 엔진 (macOS: Vision, 기타: PaddleOCR)
if [[ "$OSTYPE" == "darwin"* ]]; then
    "$PY" -c "import Vision, Quartz; print('✅ macOS Vision OCR 사용 가능')" \
        || { echo "⚠ Vision 미설치: pip install pyobjc-framework-Vision pyobjc-framework-Quartz"; }
else
    "$PY" -c "from paddleocr import PaddleOCR; print('✅ PaddleOCR 사용 가능')" \
        || { echo "❌ PaddleOCR 미설치"; exit 1; }
fi

# 4. poppler (PDF → 이미지 변환)
if command -v pdftoppm >/dev/null; then
    echo "✅ poppler (pdftoppm)"
else
    echo "❌ poppler 없음. brew install poppler"
    exit 1
fi

# 5. SQLite 스키마 + 시드
cd "$ROOT"
PYTHONPATH="$ROOT/mcp-server" "$PY" -m scripts.seed >/dev/null
"$PY" -c "
from jangbu_mcp import storage
storage.ensure_layout()
with storage.finance_conn() as conn:
    n_acc = conn.execute('SELECT COUNT(*) FROM account_mappings').fetchone()[0]
    n_rule = conn.execute('SELECT COUNT(*) FROM classification_rules').fetchone()[0]
print(f'✅ 계정 매핑: {n_acc}개')
print(f'✅ 분류 룰: {n_rule}개')
"

# 6. 단위 테스트
cd "$ROOT/mcp-server"
"$PY" -m pytest tests/ -q 2>/dev/null | tail -3
echo ""
echo "━━━ 검증 완료 ━━━"
