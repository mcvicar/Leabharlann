from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
import hashlib
import multiprocessing as mp
import re
import sys
import isbnlib
import pytesseract
from PIL import Image


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
ISBN_OCR_PATTERN = re.compile(
    r"ISBN[\s:]*([0-9Xx][-0-9Xx]{8,17}[0-9Xx])",
    re.IGNORECASE,
)
ReadFailureReason = Literal["no_isbn", "invalid_checksum", "cannot_open_image"]

@dataclass
class IsbnResult:
    isbn13: str
    isbn10: str | None
    read_method: str
    raw_value: str

@dataclass
class ReadFailure:
    source_image: str
    reason: ReadFailureReason
    detail: str
    stages_tried: list[str] = field(default_factory=list)
    raw_candidate: str | None = None
    ocr_snippet: str | None = None
    invalid_barcodes: list[str] = field(default_factory=list)

def read_isbn(image_path: Path) -> IsbnResult | ReadFailure:
    stages_tried: list[str] = []
    invalid_barcodes: list[str] = []
    ocr_snippet: str | None = None

    try:
        image = open_image(image_path)
    except Exception as exc:
        return ReadFailure(
            source_image=image_path.name,
            reason="cannot_open_image",
            detail=f"Cannot open image: {exc}",
            stages_tried=[],
        )

    pyzbar_value, pyzbar_invalid = read_isbn_from_pyzbar(image_path)
    stages_tried.append("pyzbar")
    invalid_barcodes.extend(pyzbar_invalid)

    raw_value: str | None = pyzbar_value
    read_method: str | None = "barcode" if pyzbar_value else None

    if not raw_value:
        opencv_value, opencv_invalid = read_isbn_from_opencv(image_path)
        stages_tried.append("opencv")
        invalid_barcodes.extend(opencv_invalid)
        if opencv_value:
            raw_value = opencv_value
            read_method = "barcode"

    if not raw_value:
        stages_tried.append("ocr")
        ocr_value, ocr_snippet, ocr_invalid = read_isbn_from_ocr(image)
        if ocr_value:
            raw_value = ocr_value
            read_method = "ocr"
        elif ocr_invalid:
            return ReadFailure(
                source_image=image_path.name,
                reason="invalid_checksum",
                detail=f"Invalid ISBN checksum: {ocr_invalid}",
                stages_tried=stages_tried,
                raw_candidate=ocr_invalid,
                ocr_snippet=ocr_snippet,
                invalid_barcodes=invalid_barcodes,
            )

    if not raw_value or not read_method:
        return ReadFailure(
            source_image=image_path.name,
            reason="no_isbn",
            detail="No ISBN found via barcode or OCR",
            stages_tried=stages_tried,
            ocr_snippet=ocr_snippet,
            invalid_barcodes=invalid_barcodes,
        )

    normalized = validate_and_normalize(raw_value)
    if not normalized and read_method == "ocr":
        ocr_retry = validate_ocr_candidate(raw_value)
        if ocr_retry:
            normalized, raw_value = ocr_retry

    if not normalized:
        return ReadFailure(
            source_image=image_path.name,
            reason="invalid_checksum",
            detail=f"Invalid ISBN checksum: {raw_value}",
            stages_tried=stages_tried,
            raw_candidate=raw_value,
            ocr_snippet=ocr_snippet,
            invalid_barcodes=invalid_barcodes,
        )

    isbn13, isbn10 = normalized
    return IsbnResult(
        isbn13=isbn13,
        isbn10=isbn10,
        read_method=read_method,
        raw_value=raw_value,
    )

def scan_images(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise FileNotFoundError(f"Directory not found: {directory}")

    images = [
        path
        for path in sorted(directory.iterdir())
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return images


def open_image(image_path: Path) -> Image.Image:
    with Image.open(image_path) as image:
        return image.convert("RGB")


def validate_and_normalize(raw_value: str) -> tuple[str, str | None] | None:
    """Validate a candidate ISBN and return (isbn13, isbn10) if it checks out.

    isbn10 is None when the ISBN-13 has no ISBN-10 equivalent (979-prefix).
    """
    candidate = isbnlib.canonical(raw_value)
    if not candidate:
        return None

    if len(candidate) == 13 and isbnlib.is_isbn13(candidate):
        return candidate, isbnlib.to_isbn10(candidate) or None

    if len(candidate) == 10 and isbnlib.is_isbn10(candidate):
        isbn13 = isbnlib.to_isbn13(candidate)
        return (isbn13, candidate) if isbn13 else None

    return None


def validate_ocr_candidate(candidate: str) -> tuple[tuple[str, str | None], str] | None:
    """
    Validate an OCR ISBN candidate, tolerating trailing barcode digits on later lines.
    Returns ((isbn13, isbn10), raw_value_used) or None.
    """
    first_line = candidate.split("\n")[0].strip()
    for attempt in (first_line, candidate.strip()):
        result = validate_and_normalize(attempt)
        if result:
            return result, attempt

    for isbnlike in isbnlib.get_isbnlike(first_line, level="normal"):
        result = validate_and_normalize(isbnlike)
        if result:
            return result, first_line
    return None


def image_sha256(image_path: Path) -> str:
    digest = hashlib.sha256()
    with image_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_valid_ean13(value: str) -> bool:
    return len(value) == 13 and value.isdigit() and value.startswith(("978", "979"))


def _decode_pyzbar_barcodes(image_path: Path) -> tuple[str | None, list[str]]:
    from pyzbar.pyzbar import ZBarSymbol, decode as decode_barcodes

    valid: str | None = None
    invalid: list[str] = []
    with Image.open(image_path) as image:
        rgb = image.convert("RGB")
    for barcode in decode_barcodes(rgb, symbols=[ZBarSymbol.EAN13]):
        value = barcode.data.decode("utf-8", errors="ignore").strip()
        if _is_valid_ean13(value):
            if valid is None:
                valid = value
        elif value.isdigit() and len(value) in (5, 13):
            invalid.append(value)
    return valid, invalid


def _pyzbar_worker(image_path: str, result_queue: mp.Queue) -> None:
    try:
        valid, invalid = _decode_pyzbar_barcodes(Path(image_path))
        result_queue.put({"valid": valid, "invalid": invalid})
    except Exception:
        result_queue.put({"valid": None, "invalid": []})


def read_isbn_from_pyzbar_inprocess(image_path: Path) -> tuple[str | None, list[str]]:
    try:
        return _decode_pyzbar_barcodes(image_path)
    except Exception:
        return None, []


def read_isbn_from_pyzbar_subprocess(image_path: Path) -> tuple[str | None, list[str]]:
    ctx = mp.get_context("spawn")
    result_queue: mp.Queue = ctx.Queue()
    process = ctx.Process(target=_pyzbar_worker, args=(str(image_path), result_queue))
    process.start()
    process.join(timeout=15)
    if process.is_alive():
        process.terminate()
        process.join()
        return None, []
    if process.exitcode not in (0, None):
        return None, []
    try:
        payload = result_queue.get_nowait()
    except Exception:
        return None, []
    if not isinstance(payload, dict):
        return None, []
    valid = payload.get("valid")
    invalid = payload.get("invalid")
    if isinstance(valid, str) and _is_valid_ean13(valid):
        valid_value: str | None = valid
    else:
        valid_value = None
    invalid_values = [v for v in invalid if isinstance(v, str)] if isinstance(invalid, list) else []
    return valid_value, invalid_values


def read_isbn_from_pyzbar(image_path: Path) -> tuple[str | None, list[str]]:
    # macOS zbar builds can segfault in-process; Linux/Pi uses in-process pyzbar.
    if sys.platform == "darwin":
        return read_isbn_from_pyzbar_subprocess(image_path)
    return read_isbn_from_pyzbar_inprocess(image_path)


def read_isbn_from_opencv(image_path: Path) -> tuple[str | None, list[str]]:
    try:
        import cv2
    except ImportError:
        return None, []

    image = cv2.imread(str(image_path))
    if image is None:
        return None, []

    valid: str | None = None
    invalid: list[str] = []
    detector = cv2.barcode.BarcodeDetector()
    result = detector.detectAndDecode(image)
    if not result:
        return None, invalid

    decoded_values: list[str] = []
    decoded = result[0]
    if isinstance(decoded, str):
        decoded_values.append(decoded)
    elif isinstance(decoded, tuple):
        decoded_values.extend(item for item in decoded if isinstance(item, str))

    for value in decoded_values:
        if _is_valid_ean13(value):
            if valid is None:
                valid = value
        elif value.isdigit() and len(value) in (5, 13):
            invalid.append(value)

    return valid, invalid


def read_isbn_from_ocr(image: Image.Image) -> tuple[str | None, str | None, str | None]:
    """Return (valid raw ISBN text, nearest OCR snippet, invalid ISBN candidate)."""
    text = pytesseract.image_to_string(image)
    ocr_snippet: str | None = None
    valid_value: str | None = None
    invalid_candidate: str | None = None

    for match in ISBN_OCR_PATTERN.finditer(text):
        snippet = match.group(0).strip()
        if ocr_snippet is None:
            ocr_snippet = snippet.split("\n")[0].strip()
        candidate = match.group(1).split("\n")[0].strip()
        validated = validate_ocr_candidate(candidate)
        if validated:
            _, valid_value = validated
            break
        if invalid_candidate is None:
            invalid_candidate = candidate

    return valid_value, ocr_snippet, invalid_candidate