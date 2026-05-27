from __future__ import annotations

import json
import logging
from pathlib import Path

from offline_rag.parsers.code import LANG_MAP, parse_code
from offline_rag.parsers.docx import parse_docx
from offline_rag.parsers.image import parse_image
from offline_rag.parsers.pdf import parse_pdf
from offline_rag.parsers.text import PROSE_EXTENSIONS, parse_text

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".gif", ".webp"}

SUPPORTED_EXTENSIONS: set[str] = (
    {".pdf", ".docx", ".doc"}
    | IMAGE_EXTENSIONS
    | set(LANG_MAP.keys())
    | PROSE_EXTENSIONS
    | {".ipynb"}
)


def _parse_notebook(path: Path) -> tuple[str, dict]:
    """Extract source text from Jupyter notebook cells."""
    try:
        nb = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        parts: list[str] = []
        for cell in nb.get("cells", []):
            src = "".join(cell.get("source", []))
            if src.strip():
                parts.append(src)
        return "\n\n".join(parts), {"file_type": "code", "lang": "python"}
    except Exception as e:
        logger.warning(f"Notebook parse error {path}: {e}")
        return "", {"file_type": "code", "lang": "python"}


def parse_file(path: Path, config) -> tuple[str, dict]:  # config: Config
    """Dispatch to the appropriate parser based on file extension."""
    suffix = path.suffix.lower()

    try:
        if suffix == ".pdf":
            return parse_pdf(path)
        if suffix in (".docx", ".doc"):
            return parse_docx(path)
        if suffix in IMAGE_EXTENSIONS:
            if config.vision_caption_enabled:
                # Caller (indexer) handles vision async; return sentinel
                return "", {"file_type": "image_vision", "lang": "image"}
            return parse_image(path, ocr_enabled=config.ocr_enabled)
        if suffix == ".ipynb":
            return _parse_notebook(path)
        if suffix in set(LANG_MAP.keys()):
            return parse_code(path)
        # Fallback: try reading as text
        return parse_text(path)
    except Exception as e:
        logger.warning(f"Parser error for {path}: {e}")
        return "", {}
