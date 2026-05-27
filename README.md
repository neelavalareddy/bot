# Universal Bot

A local AI agent that can do anything you ask — search the web, run code, manage files, create slides, answer questions from your documents, and more. Everything runs on your own machine via [Ollama](https://ollama.com). No paid APIs, no cloud.

**Stack:** Python 3.11 · Ollama · LanceDB · FastAPI · Next.js (Vercel frontend)

---

## How it works

1. You chat through the web UI (hosted on Vercel) or the terminal
2. The bot reasons about your request and calls the right tools
3. Risky actions (running code, deleting files) require your confirmation
4. The backend streams the response back in real time

**Tools available:**

| Tool | What it does |
|---|---|
| `web_search` | DuckDuckGo search — free, no API key |
| `web_fetch` | Read any webpage |
| `run_python` | Execute Python on your machine |
| `run_shell` | Execute PowerShell on your machine |
| `read_file` | Read any file |
| `write_file` | Create or overwrite a file |
| `list_files` | List files in a directory |
| `move_file` | Move or rename |
| `delete_file` | Permanently delete |
| `install_package` | `pip install` a package |
| `make_slides` | Create `.pptx` or `.pdf` presentations |
| `rag_search` | Search your indexed local documents |
| `index_documents` | Add documents to the searchable index |

---

## Prerequisites

### 1. Ollama

Install from [ollama.com](https://ollama.com), then pull the models you want:

```powershell
ollama pull nomic-embed-text      # required for document search
ollama pull qwen2.5:14b           # recommended agent model
ollama pull qwen2.5-coder:14b     # great for code tasks
```

### 2. Python 3.11+

### 3. (Optional) Tesseract OCR — for indexing image files

```powershell
winget install UB-Mannheim.TesseractOCR
```

---

## Backend setup

```powershell
pip install uv
uv pip install -e ".[dev]"

# First-time setup
rag init

# Index your documents (optional but enables document search)
rag index ~/Documents

# Start the API server
rag serve
```

The server starts at `http://localhost:8080`. The API is OpenAI-compatible.

---

## Web UI (Vercel)

The `frontend/` directory is a Next.js app you deploy to Vercel once, then connect to your local backend.

### Deploy to Vercel

1. Push this repo to GitHub (already done)
2. Go to [vercel.com](https://vercel.com) → New Project → import this repo
3. Set **Root Directory** to `frontend`
4. Deploy — Vercel auto-detects Next.js

### Connect to your local backend

The Vercel frontend needs to reach your local machine. Use **ngrok** (free):

```powershell
# Install ngrok: https://ngrok.com/download
ngrok http 8080
# → Gives you a URL like https://abc123.ngrok-free.app
```

Or use [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) for a permanent free URL.

Then open your Vercel app → click ⚙ Settings → paste the ngrok URL → Test → Save.

---

## Terminal chat

The `rag chat` command gives you a full agent REPL in the terminal:

```powershell
rag chat                    # chat with conversation history
rag chat --model qwen2.5:14b
rag chat --fresh            # start a new conversation
```

- Conversation history is saved to `data/chat_history.json`
- Type `/fresh` to clear history mid-session
- Confirmation prompts for risky actions appear inline

---

## CLI reference

| Command | Description |
|---|---|
| `rag init` | Write default `config.yaml`, create `data/` |
| `rag index [paths...]` | Index paths (or config paths if none given) |
| `rag stats` | Show chunk count and disk usage |
| `rag chat [-m model] [--fresh]` | Terminal agent chat with history |
| `rag serve [--host] [--port]` | Start the API server |
| `rag watch [paths...]` | Index then watch for file changes |

---

## API

| Endpoint | Method | Description |
|---|---|---|
| `/v1/chat/completions` | POST | OpenAI-compatible chat (streaming). Agent is on by default. |
| `/v1/models` | GET | List available Ollama models |
| `/index` | POST | Background reindex. Body: `{"paths": [...]}` |
| `/index/{job_id}` | GET | Poll index job status |
| `/status` | GET | Index stats |

Query params:
- `?agent=false` — bypass agent, use plain RAG only
- `?agent=false&rag=false` — plain LLM, no retrieval

---

## config.yaml

```yaml
paths:
  - ~/Documents
  - ~/Projects
embedding_model: nomic-embed-text
default_chat_model: qwen2.5:14b
chunk_size_tokens: 800
chunk_overlap_tokens: 100
top_k_dense: 20
top_k_sparse: 20
final_k: 8
context_budget_tokens: 6000
ocr_enabled: true
ollama_base_url: http://localhost:11434
server_host: localhost
server_port: 8080
data_dir: ./data
```

---

## Project structure

```
bot/
  pyproject.toml
  config.yaml
  src/offline_rag/
    cli.py            # Click CLI
    config.py         # Config dataclass
    ollama_client.py  # Async Ollama HTTP client
    server.py         # FastAPI server (OpenAI-compatible)
    retrieval.py      # Hybrid search + RRF
    store.py          # LanceDB vector store
    indexer.py        # File walker + embedder
    chunker.py        # Prose + AST code chunker
    watcher.py        # Watchdog file watcher
    parsers/          # PDF, DOCX, image, code, text
    agent/
      loop.py         # ReAct agent loop (streaming)
      tools.py        # All tool implementations
  frontend/           # Next.js app — deploy to Vercel
    app/
    components/
  tests/
```

---

## Running tests

```powershell
pytest -v
```

---

## Hardware

Tested on **Ryzen 9 9900 + RTX 5060 Ti 16 GB**

- `nomic-embed-text` runs on CPU at ~500 docs/min
- `qwen2.5:14b` at Q4 fits in 16 GB VRAM comfortably
- GPU offload is automatic via Ollama
