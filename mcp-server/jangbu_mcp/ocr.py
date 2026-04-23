"""PaddleOCR 기반 문서 OCR + 구조화.

- PaddleOCR은 로컬 실행, 외부 전송 없음
- PP-OCRv4 한국어 모델 사용
- 문서 유형: receipt / tax_invoice / bank_statement_scan
- 1단계: 텍스트 + 좌표 추출
- 2단계: 룰 기반 구조화 (정규식)
- 3단계: 룰 실패 시 LLM fallback (마스킹된 텍스트만, MCP 서버 외부에서 수행)
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Iterator

try:
    from paddleocr import PaddleOCR
except ImportError:  # 런타임 의존성. 설치 전 import 실패 방지.
    PaddleOCR = None

try:
    from pdf2image import convert_from_path
except ImportError:
    convert_from_path = None

# macOS Vision 어댑터는 darwin에서만 사용
try:
    import Vision
    import Quartz
    from Foundation import NSURL
    _HAS_VISION = True
except ImportError:
    _HAS_VISION = False


_ocr_instance = None


def _get_paddle():
    global _ocr_instance
    if _ocr_instance is None:
        if PaddleOCR is None:
            raise RuntimeError(
                "paddleocr is not installed. run `pip install paddleocr paddlepaddle` first."
            )
        _ocr_instance = PaddleOCR(use_angle_cls=True, lang="korean", show_log=False)
    return _ocr_instance


@dataclass
class OcrLine:
    text: str
    box: list[list[float]]
    confidence: float


@dataclass
class OcrResult:
    lines: list[OcrLine] = field(default_factory=list)
    full_text: str = ""
    source_path: str = ""

    def to_dict(self) -> dict:
        return {
            "source_path": self.source_path,
            "full_text": self.full_text,
            "lines": [
                {"text": l.text, "confidence": l.confidence, "box": l.box}
                for l in self.lines
            ],
        }


# ---- macOS Vision 어댑터 ----

def _ocr_vision_image(image_path: str) -> list[OcrLine]:
    """macOS Vision으로 이미지 1장 OCR. 한국어 + 영어."""
    if not _HAS_VISION:
        raise RuntimeError("macOS Vision framework not available")

    url = NSURL.fileURLWithPath_(image_path)
    source = Quartz.CGImageSourceCreateWithURL(url, None)
    if source is None:
        return []
    cg_image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
    if cg_image is None:
        return []

    img_w = Quartz.CGImageGetWidth(cg_image)
    img_h = Quartz.CGImageGetHeight(cg_image)

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(["ko-KR", "en-US"])
    request.setUsesLanguageCorrection_(True)

    success, error = handler.performRequests_error_([request], None)
    if not success:
        return []

    lines: list[OcrLine] = []
    for obs in request.results() or []:
        top = obs.topCandidates_(1)
        if not top:
            continue
        cand = top[0]
        text = cand.string()
        conf = float(cand.confidence())
        # boundingBox는 정규화된 좌표(0~1), y는 bottom-up
        bb = obs.boundingBox()
        x0 = bb.origin.x * img_w
        y0 = (1.0 - bb.origin.y - bb.size.height) * img_h  # top-down
        x1 = (bb.origin.x + bb.size.width) * img_w
        y1 = (1.0 - bb.origin.y) * img_h
        box = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
        lines.append(OcrLine(text=text, box=box, confidence=conf))

    return lines


def _engine_auto() -> str:
    """환경에 맞는 엔진 자동 선택. 환경변수 FINANCE_OCR_ENGINE으로 강제 가능."""
    import os, sys
    forced = os.environ.get("FINANCE_OCR_ENGINE")
    if forced in ("paddle", "vision"):
        return forced
    if sys.platform == "darwin" and _HAS_VISION:
        return "vision"
    return "paddle"


def run_ocr(file_path: Path, engine: str | None = None) -> OcrResult:
    """이미지 또는 PDF에서 텍스트 추출.

    engine:
      - None: 자동 (macOS면 vision, 아니면 paddle)
      - "vision": macOS Vision framework
      - "paddle": PaddleOCR
    """
    chosen = engine or _engine_auto()

    images: list[str] = []
    temp_files: list[Path] = []

    if file_path.suffix.lower() == ".pdf":
        if convert_from_path is None:
            raise RuntimeError("pdf2image not installed. run `pip install pdf2image` and brew install poppler.")
        for i, img in enumerate(convert_from_path(str(file_path), dpi=200)):
            tmp = file_path.parent / f".{file_path.stem}_p{i}.png"
            img.save(tmp, "PNG")
            images.append(str(tmp))
            temp_files.append(tmp)
    else:
        # 이미지 파일: 다양한 포맷을 PNG로 정규화 (HEIC/HEIF/WEBP/TIFF/BMP/GIF)
        from jangbu_mcp import file_types
        kind = file_types.detect_kind(file_path)
        if kind == "image":
            normalized = file_types.normalize_to_png(file_path)
            images = [str(normalized)]
            if normalized != file_path:
                temp_files.append(normalized)
        else:
            images = [str(file_path)]

    result = OcrResult(source_path=str(file_path))
    # 페이지별 y 오프셋 — 여러 페이지 PDF에서 페이지 경계 행들이 섞이지 않도록
    PAGE_Y_OFFSET = 100_000.0

    try:
        if chosen == "vision":
            for page_idx, img in enumerate(images):
                offset = page_idx * PAGE_Y_OFFSET
                for line in _ocr_vision_image(img):
                    if offset:
                        line.box = [[p[0], p[1] + offset] for p in line.box]
                    result.lines.append(line)
        else:
            paddle = _get_paddle()
            for page_idx, img in enumerate(images):
                offset = page_idx * PAGE_Y_OFFSET
                ocr_res = paddle.ocr(str(img), cls=True)
                if not ocr_res or not ocr_res[0]:
                    continue
                for line in ocr_res[0]:
                    box, (text, conf) = line
                    if offset:
                        box = [[p[0], p[1] + offset] for p in box]
                    result.lines.append(OcrLine(text=text, box=box, confidence=float(conf)))
    finally:
        for tmp in temp_files:
            try:
                tmp.unlink()
            except Exception:
                pass

    result.full_text = "\n".join(l.text for l in result.lines)
    return result


# ---- 문서 유형별 구조화 (룰 기반) ----

_BIZ_ID = re.compile(r"(\d{3}-\d{2}-\d{5})")
_AMOUNT = re.compile(r"([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)\s*원?")
# 카드명세서 금액: 쉼표 포함 또는 1~7자리(최대 999만원대) — 사업자번호(10자리 연속)와 구분
_CARD_AMOUNT = re.compile(r"^-?[0-9]{1,3}(?:,[0-9]{3})+$|^-?[0-9]{1,7}$")
_DATE = re.compile(r"(20\d{2}[-./]\d{1,2}[-./]\d{1,2})")


def _parse_amount(s: str) -> Decimal | None:
    s = s.replace(",", "").replace("원", "").strip()
    try:
        return Decimal(s)
    except Exception:
        return None


def structure_receipt(ocr: OcrResult) -> dict:
    """영수증 구조화. 상호·사업자번호·날짜·합계·공급가액·부가세 추출."""
    text = ocr.full_text
    biz_id = _BIZ_ID.search(text)
    date = _DATE.search(text)

    # 합계 라인 탐색 (합계/총액/TOTAL 키워드 + 가장 큰 금액)
    total = None
    for line in ocr.lines:
        if any(k in line.text for k in ("합계", "총액", "TOTAL", "결제금액", "받을금액")):
            nums = _AMOUNT.findall(line.text)
            if nums:
                candidates = [_parse_amount(n) for n in nums]
                candidates = [c for c in candidates if c]
                if candidates:
                    total = max(candidates)
                    break

    # 공급가액·부가세
    supply = None
    vat = None
    for line in ocr.lines:
        if "공급가액" in line.text:
            nums = _AMOUNT.findall(line.text)
            if nums:
                supply = _parse_amount(nums[-1])
        if "부가세" in line.text or "VAT" in line.text.upper():
            nums = _AMOUNT.findall(line.text)
            if nums:
                vat = _parse_amount(nums[-1])

    # 상호: 첫 3줄 중 사업자번호 없는 줄
    merchant = None
    for line in ocr.lines[:5]:
        if _BIZ_ID.search(line.text):
            continue
        if _AMOUNT.search(line.text) and len(line.text) < 20:
            continue
        merchant = line.text.strip()
        if merchant:
            break

    return {
        "doc_type": "receipt",
        "merchant": merchant,
        "biz_id": biz_id.group(1) if biz_id else None,
        "date": date.group(1).replace(".", "-").replace("/", "-") if date else None,
        "total": str(total) if total else None,
        "supply_amount": str(supply) if supply else None,
        "vat": str(vat) if vat else None,
        "raw_text": text,
        "needs_llm_fallback": total is None or merchant is None,
    }


def structure_tax_invoice(ocr: OcrResult) -> dict:
    """세금계산서 구조화. 공급자·공급받는자·공급가액·세액·작성일자."""
    text = ocr.full_text
    biz_ids = _BIZ_ID.findall(text)
    date = _DATE.search(text)

    supply_amount = None
    tax_amount = None
    for line in ocr.lines:
        if "공급가액" in line.text:
            nums = _AMOUNT.findall(line.text)
            if nums:
                supply_amount = _parse_amount(nums[-1])
        if "세액" in line.text and "공급" not in line.text:
            nums = _AMOUNT.findall(line.text)
            if nums:
                tax_amount = _parse_amount(nums[-1])

    return {
        "doc_type": "tax_invoice",
        "supplier_biz_id": biz_ids[0] if len(biz_ids) >= 1 else None,
        "recipient_biz_id": biz_ids[1] if len(biz_ids) >= 2 else None,
        "issue_date": date.group(1).replace(".", "-").replace("/", "-") if date else None,
        "supply_amount": str(supply_amount) if supply_amount else None,
        "tax_amount": str(tax_amount) if tax_amount else None,
        "raw_text": text,
        "needs_llm_fallback": supply_amount is None,
    }


def structure_bank_scan(ocr: OcrResult) -> list[dict]:
    """스캔된 통장/거래명세서 구조화. 각 거래행을 리스트로 반환."""
    # 스캔본은 테이블 구조 복원이 난이도 높아 MVP는 LLM fallback 위주로 운영
    return [
        {
            "doc_type": "bank_statement_scan",
            "raw_text": ocr.full_text,
            "needs_llm_fallback": True,
        }
    ]


# ---- 카드명세서(스캔) 구조화 ----

# 행 시작 패턴: 이용일(YYYY.MM.DD)
_CARD_DATE_LINE = re.compile(r"^\s*(20\d{2}[.\-]\d{2}[.\-]\d{2})")
_BIZ_ID_10 = re.compile(r"(\d{10})")  # 구분자 없는 10자리 사업자번호 (카드명세서 형식)


def _detect_card_issuer(full_text: str) -> str:
    """카드사 자동 탐지. 발급사별 표 포맷이 다르므로 파서 분기용."""
    t = full_text.replace(" ", "")
    if "신한카드" in t or "SHINHANCARD" in t.upper():
        return "shinhan"
    if "국민카드" in t or "KB카드" in t or "KBCARD" in t.upper():
        return "kb"
    if "삼성카드" in t:
        return "samsung"
    if "현대카드" in t:
        return "hyundai"
    if "롯데카드" in t:
        return "lotte"
    if "우리카드" in t:
        return "woori"
    if "하나카드" in t:
        return "hana"
    if "BC카드" in t or "비씨카드" in t:
        return "bc"
    return "generic"


# 카드 last3: OCR 오인식 허용 (g/q→9, o/O→0, l/I→1, B→8)
# 이용카드 식별 패턴 (발급사별)
# 각 패턴은 (전체 매칭, 카드 식별 숫자) 2 그룹을 캡처
# 숫자 자리에 OCR 오인식 문자(S/O/g/l/I/B/D/q/Q/|·서·공백 등) 허용
_CARD_ID_CHAR = r"[\d서oOqQgGlIBSsDd\|]"
_CARD_MARKERS: dict[str, re.Pattern] = {
    # 신한카드: "본인 039 / 복수 122 / 가족 200"
    "shinhan": re.compile(rf"(본인|복수|가족)\s*0?({_CARD_ID_CHAR}{{3}})"),
    # KB국민카드: "KB국민 9012" 또는 "국민 9012"
    "kb": re.compile(rf"(?:KB국민|국민|KB|kb국민|kb)\s*\**-?\**-?\**-?({_CARD_ID_CHAR}{{3,4}})", re.IGNORECASE),
    # 삼성카드
    "samsung": re.compile(rf"(?:삼성카드|SAMSUNG|삼성)\s*\**-?\**-?\**-?({_CARD_ID_CHAR}{{3,4}})", re.IGNORECASE),
    # 현대카드: "현대카드 1234", "현대카드 M 1234", "현대 M 1234"
    "hyundai": re.compile(rf"(?:현대카드|현대)\s*(?:[MXmx]\s+)?\**-?\**-?\**-?({_CARD_ID_CHAR}{{3,4}})"),
    # 롯데카드
    "lotte": re.compile(rf"(?:롯데카드|롯데\s*카드|롯데)\s*\**-?\**-?\**-?({_CARD_ID_CHAR}{{3,4}})"),
    # BC(비씨)카드
    "bc": re.compile(rf"(?:BC카드|BC|비씨카드|비씨|bc카드|bc)\s*\**-?\**-?\**-?({_CARD_ID_CHAR}{{3,4}})", re.IGNORECASE),
    # 우리카드
    "woori": re.compile(rf"(?:우리카드|우리)\s*\**-?\**-?\**-?({_CARD_ID_CHAR}{{3,4}})"),
    # 하나카드
    "hana": re.compile(rf"(?:하나카드|하나)\s*\**-?\**-?\**-?({_CARD_ID_CHAR}{{3,4}})"),
}

# 범용 카드번호 끝자리 패턴 (발급사 감지 실패 시 fallback)
_CARD_TAIL_GENERIC = re.compile(r"(?:\*{3,}[- ]?|X{3,}[- ]?|[카카드드][- ]?)(\d{3,4})\b")

_PRODUCT_KEYWORDS = (
    "일시불", "할부", "체크", "현금서비스", "해외",
    "CHECK", "INSTALL",
)


def _fix_card_last3(raw: str) -> str:
    """OCR 오인식 숫자 치환. 문자 → 숫자 매핑.

    자주 나오는 오인식:
      0 ↔ O, o, D (가끔)
      1 ↔ l, I, |
      5 ↔ S, s
      8 ↔ B
      9 ↔ g, G, q, Q, 서 (신한 폰트)
    """
    t = (
        raw.replace("o", "0").replace("O", "0").replace("D", "0")
           .replace("q", "9").replace("Q", "9")
           .replace("g", "9").replace("G", "9")
           .replace("l", "1").replace("I", "1").replace("|", "1")
           .replace("B", "8")
           .replace("S", "5").replace("s", "5")
           .replace("서", "9")
    )
    return t


def _fix_ocr_amount(raw: str) -> str:
    """금액 내 오인식 복구. 쉼표 안쪽의 문자도 치환."""
    return (
        raw.replace("O", "0").replace("o", "0")
           .replace("S", "5").replace("s", "5")
           .replace("l", "1").replace("I", "1").replace("|", "1")
           .replace("B", "8")
           .replace("D", "0")
    )


def _find_card_marker(flat: str, issuer: str) -> tuple[int, int, str] | None:
    """(매칭시작, 매칭끝, 카드 식별번호) 반환. 실패 시 None.

    우선순위:
    1. 발급사 전용 패턴
    2. 범용 카드번호 끝자리 패턴
    """
    pat = _CARD_MARKERS.get(issuer)
    if pat:
        m = pat.search(flat)
        if m:
            # 마지막 캡처 그룹이 식별번호
            last3 = _fix_card_last3(m.groups()[-1])
            if last3.isdigit():
                return m.start(), m.end(), last3

    # Fallback: 범용 카드번호 꼬리
    m = _CARD_TAIL_GENERIC.search(flat)
    if m:
        last3 = _fix_card_last3(m.group(1))
        if last3.isdigit():
            return m.start(), m.end(), last3

    return None


def _parse_card_row_generic(tokens: list[str], issuer: str = "generic") -> dict | None:
    """범용 카드명세서 행 파서.

    모든 한국 카드사 공통 구조에 맞춤:
      [이용일] [결제일] [이용카드] [가맹점] [금액] [상품구분] [사업자번호]

    각 발급사 전용 _CARD_MARKERS 패턴으로 카드 식별 후,
    나머지는 공통 규칙으로 가맹점·금액·사업자번호 추출.

    발급사별 실패 시 generic fallback으로 재시도.
    """
    if not tokens:
        return None

    # 이용일
    m = _CARD_DATE_LINE.match(tokens[0].strip())
    if not m:
        return None
    use_date = m.group(1).replace(".", "-")

    flat = " ".join(tokens)

    # 1) 사업자번호 — 행 끝 10자리 숫자 (한 단어)
    biz_id = None
    biz_m = None
    for bm in _BIZ_ID_10.finditer(flat):
        if len(bm.group(1)) == 10:
            biz_m = bm
            biz_id = bm.group(1)
    biz_end = biz_m.end() if biz_m else len(flat)

    # 2) 이용카드 식별 — 발급사 전용 패턴 → 범용 fallback
    marker = _find_card_marker(flat, issuer)
    if not marker:
        # 발급사 감지가 실패했을 수 있으므로 다른 발급사 패턴도 한 번씩 시도
        for alt_issuer in _CARD_MARKERS:
            if alt_issuer == issuer:
                continue
            marker = _find_card_marker(flat, alt_issuer)
            if marker:
                break
    if not marker:
        return None

    card_marker_start, card_marker_end, card_last3 = marker
    if not card_last3.isdigit():
        return None

    # 카드 식별번호 길이는 3자리(신한)~4자리(KB 등) 모두 허용
    if len(card_last3) not in (3, 4):
        return None

    after_card = flat[card_marker_end:biz_end]

    # 3) 상품 키워드 위치 — 금액 직후에 옴
    product_idx = -1
    product_name = None
    for kw in _PRODUCT_KEYWORDS:
        idx = after_card.find(kw)
        if idx >= 0 and (product_idx < 0 or idx < product_idx):
            product_idx = idx
            product_name = kw

    # 4) 금액 — 상품 키워드 직전의 쉼표 숫자 또는 1~7자리
    #    먼저 search_region에서 금액 위치만 찾고, 매칭된 텍스트는 OCR 보정 후 파싱
    search_region = after_card[:product_idx] if product_idx >= 0 else after_card
    # OCR 보정 후 재탐색 (23,OOO → 23,000)
    fixed_region = _fix_ocr_amount(search_region)
    amount_matches = list(re.finditer(r"(-?[0-9]{1,3}(?:,[0-9]{3})+|-?[0-9]{1,7})", fixed_region))
    if not amount_matches:
        return None
    last = amount_matches[-1]
    amount = _parse_amount(last.group(1))
    if amount is None:
        return None
    # 가맹점 추출은 원본(비보정) 기준이어야 한글 훼손 안됨
    merchant = search_region[:last.start()].strip()

    # 가맹점이 빈 문자열이거나 너무 짧으면 실패 처리 (노이즈 행 차단)
    if not merchant or len(merchant) < 2:
        return None

    return {
        "use_date": use_date,
        "merchant": merchant,
        "amount": str(amount),
        "biz_id": biz_id,
        "card_last3": card_last3,
        "product": product_name,
    }


# 하위 호환: 신한 전용 함수는 범용 파서 wrapper로 유지
def _parse_card_row_shinhan(tokens: list[str]) -> dict | None:
    """(deprecated wrapper) 신한카드 전용 → 범용 파서 호출."""
    return _parse_card_row_generic(tokens, issuer="shinhan")


# ---- 발급사별 특화 파서 레지스트리 ----
# 범용 파서로 해결 안 되는 발급사별 특수 포맷은 여기에 등록.
# 예: 삼성카드는 '일시불/할부' 대신 '선결제'를 씀, KB는 '체크/신용' 구분 등
# 각 함수는 tokens → dict | None 반환. 실패 시 범용 파서로 fallback.
CARD_PARSERS: dict[str, callable] = {
    "shinhan": _parse_card_row_shinhan,
    # 향후 확장:
    # "kb":      _parse_card_row_kb,
    # "samsung": _parse_card_row_samsung,
    # "hyundai": _parse_card_row_hyundai,
    # "lotte":   _parse_card_row_lotte,
    # "bc":      _parse_card_row_bc,
    # "woori":   _parse_card_row_woori,
    # "hana":    _parse_card_row_hana,
}


def _parse_card_row(tokens: list[str], issuer: str) -> dict | None:
    """카드사별 특화 파서 우선 시도 → 범용 fallback."""
    if not tokens:
        return None
    # 특화 파서가 있으면 먼저 시도
    specialized = CARD_PARSERS.get(issuer)
    if specialized:
        result = specialized(tokens)
        if result:
            return result
    # 범용
    return _parse_card_row_generic(tokens, issuer=issuer)


def _group_lines_to_rows(ocr: OcrResult, row_threshold: float = 8.0) -> list[list[str]]:
    """OCR 라인들을 y좌표 기준으로 행 단위로 묶는다.

    전략:
    - 각 라인의 y 중심값을 계산
    - 정렬 후, 첫 라인의 y를 행 앵커로 삼아 앵커±threshold 이내면 같은 행
    - 누적 평균 방식은 드리프트가 생겨 여러 행을 흡수하므로 사용 안 함
    - threshold 기본 8px (200 DPI 신한카드 표 기준 셀 내 라인 간격)
    """
    enriched = []
    for l in ocr.lines:
        ys = [p[1] for p in l.box]
        y_center = sum(ys) / len(ys)
        xs = [p[0] for p in l.box]
        x_left = min(xs)
        enriched.append((y_center, x_left, l.text))

    if not enriched:
        return []

    enriched.sort(key=lambda e: (e[0], e[1]))
    rows: list[list[tuple[float, str]]] = []
    current: list[tuple[float, str]] = []
    anchor_y: float | None = None

    for y, x, text in enriched:
        if anchor_y is None or abs(y - anchor_y) <= row_threshold:
            current.append((x, text))
            if anchor_y is None:
                anchor_y = y
        else:
            rows.append([t for _, t in sorted(current)])
            current = [(x, text)]
            anchor_y = y

    if current:
        rows.append([t for _, t in sorted(current)])

    return rows


def structure_card_statement(ocr: OcrResult) -> dict:
    """카드명세서(스캔/PDF) 구조화.

    반환:
      {
        "doc_type": "card_statement_scan",
        "issuer": "shinhan",
        "period_start": "2025-07-01",
        "period_end": "2025-12-31",
        "card_last3_list": ["039", "122"],
        "rows": [{use_date, merchant, amount, biz_id, card_last3}, ...],
        "unparsed_count": N,
        "needs_llm_fallback": bool,
        "raw_text": str,  # 마스킹 적용 후
      }
    """
    full_text = ocr.full_text
    issuer = _detect_card_issuer(full_text)

    # 기간 탐지
    period_start = None
    period_end = None
    period_match = re.search(r"(20\d{2}[./\-]\d{2}[./\-]\d{2})\s*[-~]\s*(20\d{2}[./\-]\d{2}[./\-]\d{2})", full_text)
    if period_match:
        period_start = period_match.group(1).replace(".", "-").replace("/", "-")
        period_end = period_match.group(2).replace(".", "-").replace("/", "-")

    # 메타데이터 탐지 (발급일시·결제일·결제계좌·카드회원명 마스킹 형태)
    meta: dict = {}
    issue_m = re.search(r"발행일시[:\s]*(\d{4}[년\-./]\s*\d{1,2}[월\-./]\s*\d{1,2}[일]?)", full_text)
    if issue_m:
        meta["issued_at"] = issue_m.group(1).strip()

    # 결제일 (예: "결제일: 매월 14일")
    pay_day_m = re.search(r"결제일[:\s]*(?:매월\s*)?(\d{1,2})일", full_text)
    if pay_day_m:
        meta["payment_day"] = int(pay_day_m.group(1))

    # 고객명 마스킹 형태 보존 (예: 홍*동 고객님) — 사업자 추적용
    name_m = re.search(r"([가-힣]\*+[가-힣]+)\s*고객님", full_text)
    if name_m:
        meta["customer_mask"] = name_m.group(1)

    # 행 단위 재구성
    rows = _group_lines_to_rows(ocr)

    parsed_rows = []
    unparsed_rows: list[list[str]] = []
    card_last3_set = set()

    for row_tokens in rows:
        if not row_tokens:
            continue
        first = row_tokens[0].strip()
        if not _CARD_DATE_LINE.match(first):
            continue

        # 카드사별 특화 파서 → 범용 fallback
        parsed = _parse_card_row(row_tokens, issuer=issuer)

        if parsed:
            parsed_rows.append(parsed)
            if parsed.get("card_last3"):
                card_last3_set.add(parsed["card_last3"])
        else:
            unparsed_rows.append(row_tokens)

    # 학습된 alias 자동 적용 (사용자가 이전에 승인한 통합 규칙)
    try:
        from jangbu_mcp import ocr_corrections
        cp_alias = ocr_corrections.load_alias_map("counterparty_alias")
        card_alias = ocr_corrections.load_alias_map("card_last3_alias")
        alias_applied = {"counterparty": 0, "card_last3": 0}
        for r in parsed_rows:
            if r.get("merchant") and r["merchant"] in cp_alias:
                r["merchant"] = cp_alias[r["merchant"]]
                alias_applied["counterparty"] += 1
            if r.get("card_last3") and r["card_last3"] in card_alias:
                r["card_last3"] = card_alias[r["card_last3"]]
                alias_applied["card_last3"] += 1
    except Exception:
        alias_applied = {"counterparty": 0, "card_last3": 0}

    # 카드별 건수·총액 집계 (카드 여러 장 분석용)
    card_stats: dict[str, dict] = {}
    for r in parsed_rows:
        last3 = r.get("card_last3") or "unknown"
        if last3 not in card_stats:
            card_stats[last3] = {"count": 0, "total": 0}
        card_stats[last3]["count"] += 1
        try:
            card_stats[last3]["total"] += int(r["amount"])
        except Exception:
            pass

    total_rows = len(parsed_rows) + len(unparsed_rows)
    parse_rate = len(parsed_rows) / total_rows if total_rows else 0

    return {
        "doc_type": "card_statement_scan",
        "issuer": issuer,
        "period_start": period_start,
        "period_end": period_end,
        "meta": meta,
        "card_last3_list": sorted(card_last3_set),
        "card_stats": card_stats,
        "rows": parsed_rows,
        "row_count": len(parsed_rows),
        "unparsed_count": len(unparsed_rows),
        "unparsed_rows": unparsed_rows,
        "parse_rate": round(parse_rate, 3),
        "needs_llm_fallback": parse_rate < 0.5,
        "alias_applied": alias_applied,
        "raw_text": full_text,
    }


def structure(ocr: OcrResult, doc_type: str) -> dict | list[dict]:
    if doc_type == "receipt":
        return structure_receipt(ocr)
    if doc_type == "tax_invoice":
        return structure_tax_invoice(ocr)
    if doc_type == "bank_statement_scan":
        return structure_bank_scan(ocr)
    if doc_type == "card_statement_scan":
        return structure_card_statement(ocr)
    raise ValueError(f"unknown doc_type: {doc_type}")


def receipt_to_transaction(doc: dict, account_id: str | None = None) -> dict | None:
    """영수증 구조화 결과를 표준 거래내역으로 변환."""
    if not doc.get("total") or not doc.get("date"):
        return None
    return {
        "transaction_id": f"tx_{uuid.uuid4().hex[:16]}",
        "date": doc["date"],
        "amount": doc["total"],
        "currency": "KRW",
        "direction": "outflow",
        "counterparty": doc.get("merchant") or "영수증",
        "description": f"영수증 {doc.get('merchant', '')}".strip(),
        "source": "ocr",
        "source_ref": f"receipt:{doc.get('biz_id', '')}:{doc['date']}:{doc['total']}",
        "raw_description": doc.get("raw_text", "")[:500],
        "account_id": account_id,
    }


def card_statement_to_transactions(doc: dict, account_id_base: str | None = None) -> list[dict]:
    """카드명세서 구조화 결과를 표준 거래내역 리스트로 변환.

    - 카드명세서는 환불(-) 거래도 포함될 수 있음. amount가 음수면 direction=inflow로 뒤집기.
    - source_ref: 같은 날 같은 가맹점 같은 금액 중복을 구별하기 위해 행 순번 포함.
    - account_id: account_id_base가 있으면 '{base}_{card_last3}' 조합, 없으면 'card_{last3}'.
    """
    issuer = doc.get("issuer", "unknown")
    txs: list[dict] = []
    # 같은 (date, merchant, amount) 조합 카운트 → 중복 구분용 seq
    seen_counter: dict[tuple[str, str, str], int] = {}

    for row in doc.get("rows", []):
        date_s = row.get("use_date")
        merchant = row.get("merchant")
        amount_s = row.get("amount")
        if not date_s or not merchant or amount_s is None:
            continue

        try:
            amount_dec = Decimal(amount_s)
        except Exception:
            continue
        if amount_dec == 0:
            continue

        direction = "outflow" if amount_dec > 0 else "inflow"
        amount_abs = abs(amount_dec)

        key = (date_s, merchant, str(amount_abs))
        seq = seen_counter.get(key, 0)
        seen_counter[key] = seq + 1

        card_last3 = row.get("card_last3") or "xxx"
        if account_id_base:
            account_id = f"{account_id_base}_{card_last3}"
        else:
            account_id = f"card_{issuer}_{card_last3}"

        biz_id = row.get("biz_id") or ""
        source_ref = f"card_stmt:{issuer}:{date_s}:{merchant}:{amount_abs}:{seq}"

        txs.append({
            "transaction_id": f"tx_{uuid.uuid4().hex[:16]}",
            "date": date_s,
            "amount": str(amount_abs),
            "currency": "KRW",
            "direction": direction,
            "counterparty": merchant[:100],
            "description": f"[{issuer}카드] {merchant}" + (f" (사업자 {biz_id})" if biz_id else ""),
            "source": "ocr",
            "source_ref": source_ref,
            "raw_description": f"{date_s} {merchant} {amount_s} {biz_id}".strip(),
            "account_id": account_id,
        })

    return txs
