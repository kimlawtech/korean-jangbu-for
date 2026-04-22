"""파일 유형 감지 + 이미지 정규화.

지원 확장자:
- 이미지: jpg, jpeg, png, heic, heif, webp, tiff, tif, bmp, gif
- PDF:    pdf
- 문서:   xlsx, xls, csv
- 구조화: json (향후)

이미지는 OCR 파이프라인 전에 PNG로 정규화 (Pillow).
HEIC/HEIF는 `pillow-heif` 있을 때만 지원.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

FileKind = Literal["image", "pdf", "xlsx", "csv", "json", "unknown"]

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp",
              ".tiff", ".tif", ".bmp", ".gif"}
PDF_EXTS = {".pdf"}
XLSX_EXTS = {".xlsx", ".xls", ".xlsm"}
CSV_EXTS = {".csv", ".tsv"}
JSON_EXTS = {".json"}


def detect_kind(path: Path | str) -> FileKind:
    """확장자 기반 파일 유형 감지."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in PDF_EXTS:
        return "pdf"
    if ext in XLSX_EXTS:
        return "xlsx"
    if ext in CSV_EXTS:
        return "csv"
    if ext in JSON_EXTS:
        return "json"
    return "unknown"


def _register_heif():
    """HEIC/HEIF 지원 등록. 실패해도 무시."""
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
        return True
    except Exception:
        return False


_HEIF_READY = _register_heif()


def normalize_to_png(image_path: Path) -> Path:
    """비-PNG 이미지를 PNG로 변환. 이미 PNG면 경로 그대로 반환.

    HEIC/HEIF·WEBP·TIFF·BMP·GIF → PNG
    실패 시 원본 경로 반환 (OCR 엔진이 직접 시도).
    """
    from PIL import Image

    ext = image_path.suffix.lower()
    if ext == ".png":
        return image_path
    if ext in (".heic", ".heif") and not _HEIF_READY:
        raise RuntimeError(
            f"HEIC/HEIF 지원 미설치. 'pip install pillow-heif' 후 재시도. 파일: {image_path.name}"
        )

    try:
        img = Image.open(image_path)
        # GIF·PNG 다중 프레임은 첫 프레임만
        if getattr(img, "is_animated", False):
            img.seek(0)
        # 일부 포맷(HEIC)은 RGBA 아닌 경우 RGB로 변환 필요
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        out = image_path.parent / f".{image_path.stem}_norm.png"
        img.save(out, "PNG", optimize=True)
        return out
    except Exception:
        return image_path


def list_supported_extensions() -> dict:
    """UI·스킬 문서 표시용."""
    return {
        "image": sorted(IMAGE_EXTS),
        "pdf": sorted(PDF_EXTS),
        "xlsx": sorted(XLSX_EXTS),
        "csv": sorted(CSV_EXTS),
        "heif_ready": _HEIF_READY,
    }


def scan_folder(folder: Path, recursive: bool = True) -> dict[FileKind, list[Path]]:
    """폴더 내 파일을 유형별로 분류."""
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(f"not a directory: {folder}")

    result: dict[FileKind, list[Path]] = {
        "image": [], "pdf": [], "xlsx": [], "csv": [], "json": [], "unknown": []
    }
    pattern = "**/*" if recursive else "*"
    for p in folder.glob(pattern):
        if p.is_file() and not p.name.startswith("."):
            result[detect_kind(p)].append(p)
    return result
