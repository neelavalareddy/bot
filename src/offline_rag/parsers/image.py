from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def parse_image(path: Path, ocr_enabled: bool = True) -> tuple[str, dict]:
    if not ocr_enabled:
        return "", {"file_type": "image", "lang": "image"}

    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        logger.warning("pytesseract/Pillow not installed; skipping image OCR")
        return "", {"file_type": "image", "lang": "image"}

    try:
        img = Image.open(str(path))
        text = pytesseract.image_to_string(img)
        return text.strip(), {"file_type": "text", "lang": "image"}
    except Exception as e:
        logger.warning(f"OCR failed for {path}: {e}")
        return "", {"file_type": "image", "lang": "image"}
