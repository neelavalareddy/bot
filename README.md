# Offline RAG

Fully offline personal document RAG chatbot. No API keys, no cloud — everything runs locally via [Ollama](https://ollama.com).

**Stack:** Python 3.11 · Ollama · LanceDB · FastAPI · Open WebUI compatible

---

## Prerequisites

### 1. Ollama

Install Ollama from https://ollama.com then pull the required models:

```bash
ollama pull nomic-embed-text      # embeddings (required)
ollama pull qwen2.5:14b           # default chat model
ollama pull qwen2.5-coder:14b     # great for code repos
ollama pull llama3.1:8b           # lighter alternative

# Optional — MoE model, excellent on 32 GB+ RAM
ollama pull qwen3:30b-a3b
```

**Recommended Ollama env vars** (set before starting Ollama):

```bash
export OLLAMA_MAX_LOADED_MODELS=3
export OLLAMA_FLASH_ATTENTION=1
export OLLAMA_KV_CACHE_TYPE=q8_0
export OLLAMA_KEEP_ALIVE=30m
```

On Windows (PowerShell):
```powershell
$env:OLLAMA_MAX_LOADED_MODELS = "3"
$env:OLLAMA_FLASH_ATTENTION  = "1"
$env:OLLAMA_KV_CACHE_TYPE    = "q8_0"
$env:OLLAMA_KEEP_ALIVE       = "30m"
```

### 2. Tesseract OCR (optional, for image files)

**Ubuntu/Debian:**
```bash
sudo apt install tesseract-ocr
```

**macOS:**
```bash
brew install tesseract
```

**Windows:**
```powershell
winget install UB-Mannheim.TesseractOCR
```

After installing, ensure `tesseract` is on your PATH. Disable OCR in `config.yaml` (`ocr_enabled: false`) if you skip this.

---

## Installation

### With uv (recommended)

```bash
pip install uv        # one-time
uv pip install -e ".[dev]"
```

### With pip

```bash
pip install -e ".[dev]"
```

---

## Quickstart

```bash
# 1. Initialise config and directories
rag init

# 2. Edit config.yaml — set your paths, model preferences
#    Default paths: ~/Documents, ~/Projects

# 3. Index your files
rag index ~/Documents

# 4. Start the server (Open WebUI or any OpenAI client)
rag serve

# — or — chat directly in the terminal
rag chat
```

---

## Open WebUI integration

1. In Open WebUI → Settings → Connections → add an OpenAI-compatible connection:
   - **API Base URL:** `http://localhost:8080/v1`
   - **API Key:** `ollama` (any non-empty string)
2. All your locally-pulled Ollama models appear in the model dropdown.
3. Every chat automatically gets RAG applied — retrieved context is injected transparently.
4. Append `?rag=false` to the API URL to bypass retrieval for a session.

---

## CLI reference

| Command | Description |
|---|---|
| `rag init` | Write default `config.yaml`, create `data/` directories |
| `rag index [paths...]` | Index paths (or config paths if none given) |
| `rag stats` | Show file/chunk counts and disk usage |
| `rag chat [-m model]` | Interactive REPL with streaming + source citations |
| `rag serve [--host] [--port]` | Start FastAPI server on `localhost:8080` |
| `rag watch [paths...]` | Index then watch for changes (incremental re-index) |

---

## API reference

| Endpoint | Method | Description |
|---|---|---|
| `/v1/chat/completions` | POST | OpenAI-compatible chat (streaming supported). Add `?rag=false` to bypass retrieval. |
| `/v1/models` | GET | List all Ollama models (proxied) |
| `/index` | POST | Trigger background reindex. Body: `{"paths": [...]}` optional |
| `/index/{job_id}` | GET | Poll background index job status |
| `/status` | GET | Index stats: file count, chunk count, disk usage |

---

## config.yaml reference

```yaml
paths:
  - ~/Documents          # directories to index (~ expanded)
  - ~/Projects
ignore_patterns:         # gitignore-syntax patterns to skip
  - "*.log"
  - "node_modules/**"
embedding_model: nomic-embed-text   # Ollama model for embeddings
default_chat_model: qwen2.5:14b     # default chat model
chunk_size_tokens: 800              # prose chunk target size (tokens ≈ chars/4)
chunk_overlap_tokens: 100           # overlap between consecutive chunks
top_k_dense: 20                     # dense retrieval candidates
top_k_sparse: 20                    # BM25 retrieval candidates
final_k: 8                          # chunks after RRF fusion
context_budget_tokens: 6000         # max context injected into prompt
ocr_enabled: true                   # OCR images via Tesseract
vision_caption_enabled: false       # use vision model instead of OCR
vision_model: llama3.2-vision:11b   # vision model for image captioning
ollama_base_url: http://localhost:11434
server_host: localhost
server_port: 8080
embedding_batch_size: 32
data_dir: ./data                    # LanceDB and state.db location
```

---

## Ignore files

Create a `.ragignore` file in any indexed directory using gitignore syntax:

```
*.secret
private/**
*.key
```

---

## Project structure

```
offline-rag/
  pyproject.toml
  config.yaml
  src/offline_rag/
    cli.py          # Click CLI entry point
    config.py       # Config dataclass + loader
    ollama_client.py # Async HTTP client for Ollama
    chunker.py      # Prose + AST-aware code chunker
    store.py        # LanceDB wrapper, hybrid search
    indexer.py      # File walker, hash state, embedder
    retrieval.py    # Hybrid search → RRF → context builder
    server.py       # FastAPI OpenAI-compatible server
    watcher.py      # Watchdog incremental re-index
    parsers/
      __init__.py   # Extension dispatch
      text.py       # Plain text / markdown
      pdf.py        # PyMuPDF
      docx.py       # python-docx
      image.py      # pytesseract OCR
      code.py       # Source code (AST + line-based)
  data/
    lancedb/        # Vector store (auto-created)
    state.db        # File hash state (auto-created)
  tests/
    test_chunker.py
    test_retrieval.py
```

---

## Running tests

```bash
pytest -v
```

---

## Hardware notes

Tested target: **Ryzen 9 9900 + RTX 5060 Ti 16 GB**

- `nomic-embed-text` runs entirely on CPU at ~500 docs/min.
- `qwen2.5:14b` fits comfortably in 16 GB VRAM at Q4 quantisation.
- For larger models (30B+), try `qwen3:30b-a3b` which uses only ~3 B active params via MoE.
- GPU offload is automatic via Ollama — no configuration needed.
