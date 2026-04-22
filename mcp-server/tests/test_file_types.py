"""파일 유형 감지 테스트."""
import tempfile
from pathlib import Path

from jangbu_mcp import file_types


def test_detect_image_extensions():
    for ext in (".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp",
                ".tiff", ".tif", ".bmp", ".gif"):
        assert file_types.detect_kind(f"test{ext}") == "image", ext


def test_detect_pdf():
    assert file_types.detect_kind("doc.pdf") == "pdf"
    assert file_types.detect_kind("DOC.PDF") == "pdf"


def test_detect_xlsx():
    assert file_types.detect_kind("book.xlsx") == "xlsx"
    assert file_types.detect_kind("old.xls") == "xlsx"
    assert file_types.detect_kind("macro.xlsm") == "xlsx"


def test_detect_csv():
    assert file_types.detect_kind("data.csv") == "csv"
    assert file_types.detect_kind("tab.tsv") == "csv"


def test_detect_unknown():
    assert file_types.detect_kind("file.txt") == "unknown"
    assert file_types.detect_kind("noext") == "unknown"


def test_scan_folder_categorizes():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "a.jpg").touch()
        (root / "b.png").touch()
        (root / "c.pdf").touch()
        (root / "d.xlsx").touch()
        (root / "e.csv").touch()
        (root / "f.txt").touch()
        (root / ".hidden.jpg").touch()  # 숨김 파일은 제외

        result = file_types.scan_folder(root, recursive=False)
        assert len(result["image"]) == 2
        assert len(result["pdf"]) == 1
        assert len(result["xlsx"]) == 1
        assert len(result["csv"]) == 1
        assert len(result["unknown"]) == 1


def test_scan_folder_recursive():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        sub = root / "sub"
        sub.mkdir()
        (root / "top.jpg").touch()
        (sub / "nested.png").touch()

        result = file_types.scan_folder(root, recursive=True)
        assert len(result["image"]) == 2


def test_scan_folder_non_dir_raises():
    import pytest
    with pytest.raises(ValueError):
        file_types.scan_folder(Path("/nonexistent/xyz"))


def test_list_supported_extensions():
    info = file_types.list_supported_extensions()
    assert ".png" in info["image"]
    assert ".heic" in info["image"]
    assert ".pdf" in info["pdf"]
    assert isinstance(info["heif_ready"], bool)
