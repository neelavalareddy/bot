from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_PATH = Path("config.yaml")

DEFAULT_CONFIG_YAML = """\
paths:
  - ~/Documents
  - ~/Projects
ignore_patterns:
  - "*.log"
  - "node_modules/**"
  - ".git/**"
  - "__pycache__/**"
  - ".venv/**"
  - "venv/**"
  - "*.pyc"
  - "*.pyo"
  - "*.egg-info/**"
  - "dist/**"
  - "build/**"
embedding_model: nomic-embed-text
default_chat_model: qwen2.5:14b
chunk_size_tokens: 800
chunk_overlap_tokens: 100
top_k_dense: 20
top_k_sparse: 20
final_k: 8
context_budget_tokens: 6000
ocr_enabled: true
vision_caption_enabled: false
vision_model: llama3.2-vision:11b
ollama_base_url: http://localhost:11434
server_host: localhost
server_port: 8080
embedding_batch_size: 32
data_dir: ./data
"""

_FIELDS = {
    "paths", "ignore_patterns", "embedding_model", "default_chat_model",
    "chunk_size_tokens", "chunk_overlap_tokens", "top_k_dense", "top_k_sparse",
    "final_k", "context_budget_tokens", "ocr_enabled", "vision_caption_enabled",
    "vision_model", "ollama_base_url", "server_host", "server_port",
    "embedding_batch_size", "data_dir",
}


@dataclass
class Config:
    paths: list[str] = field(default_factory=lambda: ["~/Documents"])
    ignore_patterns: list[str] = field(default_factory=list)
    embedding_model: str = "nomic-embed-text"
    default_chat_model: str = "qwen2.5:14b"
    chunk_size_tokens: int = 800
    chunk_overlap_tokens: int = 100
    top_k_dense: int = 20
    top_k_sparse: int = 20
    final_k: int = 8
    context_budget_tokens: int = 6000
    ocr_enabled: bool = True
    vision_caption_enabled: bool = False
    vision_model: str = "llama3.2-vision:11b"
    ollama_base_url: str = "http://localhost:11434"
    server_host: str = "localhost"
    server_port: int = 8080
    embedding_batch_size: int = 32
    data_dir: str = "./data"


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        return Config()
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return Config(**{k: v for k, v in data.items() if k in _FIELDS})
