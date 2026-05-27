from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def parse_pdf(path: Path) -> tuple[str, dict]:
    try:
        import fitz  # pymupdf
    except ImportError:
        logger.error("pymupdf not installed; cannot parse PDF")
        return "", {"file_type": "pdf", "lang": "pdf"}

    parts: list[str] = []
    try:
        doc = fitz.open(str(path))
        for page in doc:
            parts.append(page.get_text())
        doc.close()
    except Exception as e:
        logger.warning(f"PDF parse error {path}: {e}")

    return "\n\n".join(parts), {"file_type": "text", "lang": "pdf"}
