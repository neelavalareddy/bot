from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

import click
from rich.console import Console

console = Console()


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "httpcore", "watchdog", "lancedb", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.ERROR)


def _runtime():
    from offline_rag.agent.skills import SkillRegistry
    from offline_rag.config import load_config
    from offline_rag.ollama_client import OllamaClient
    from offline_rag.store import VectorStore

    config = load_config()
    ollama = OllamaClient(config.ollama_base_url)
    store = VectorStore(f"{config.data_dir}/lancedb")
    skill_registry = SkillRegistry(Path(config.data_dir) / "skills")
    return config, ollama, store, skill_registry


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.option("--verbose", "-v", is_flag=True, default=False)
def cli(verbose: bool) -> None:
    """Universal Bot — local AI agent with tools, powered by Ollama."""
    _setup_logging(verbose)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@cli.command()
def init() -> None:
    """Write default config.yaml and create data directories."""
    from offline_rag.config import DEFAULT_CONFIG_YAML

    config_path = Path("config.yaml")
    if config_path.exists():
        console.print("[yellow]config.yaml already exists — skipping.[/yellow]")
    else:
        config_path.write_text(DEFAULT_CONFIG_YAML)
        console.print("[green]✓[/green] Created config.yaml")

    Path("data/lancedb").mkdir(parents=True, exist_ok=True)
    console.print("[green]✓[/green] Created data/ directories")
    console.print("\n[bold]Next steps:[/bold]")
    console.print("  [cyan]rag index ~/Documents[/cyan]   — index your files")
    console.print("  [cyan]rag serve[/cyan]               — start the API server")
    console.print("  [cyan]rag chat[/cyan]                — chat in the terminal")


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("paths", nargs=-1)
def index(paths: tuple[str, ...]) -> None:
    """Index files. Pass paths or use paths from config.yaml."""

    async def _run() -> dict:
        config, ollama, store, _ = _runtime()
        from offline_rag.indexer import FileIndexer
        indexer = FileIndexer(config, store, ollama)
        try:
            return await indexer.index_paths(list(paths) if paths else None)
        finally:
            await ollama.aclose()

    result = asyncio.run(_run())
    console.print("\n[bold]Indexing complete[/bold]")
    console.print(f"  Indexed : [green]{result['indexed']}[/green] files")
    console.print(f"  Skipped : {result['skipped']} (unchanged)")
    console.print(f"  Deleted : {result['deleted']} (removed from disk)")
    console.print(f"  Errors  : [{'red' if result['errors'] else 'default'}]{result['errors']}[/]")


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

@cli.command()
def stats() -> None:
    """Show index statistics."""
    from offline_rag.config import load_config
    from offline_rag.store import VectorStore

    config = load_config()
    store = VectorStore(f"{config.data_dir}/lancedb")
    s = store.get_stats()

    db_path = Path(config.data_dir) / "lancedb"
    disk_bytes = (
        sum(f.stat().st_size for f in db_path.rglob("*") if f.is_file())
        if db_path.exists()
        else 0
    )

    console.print(f"[bold]Files indexed:[/bold] {s['unique_files']}")
    console.print(f"[bold]Total chunks: [/bold] {s['total_chunks']}")
    console.print(f"[bold]Disk usage:   [/bold] {disk_bytes / 1024 / 1024:.1f} MB")


# ---------------------------------------------------------------------------
# chat  — full agent REPL with persistent history
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--model", "-m", default=None, help="Ollama model (overrides config)")
@click.option("--fresh", is_flag=True, help="Ignore saved history and start a new conversation")
def chat(model: str | None, fresh: bool) -> None:
    """Interactive agent chat — web search, code, files, slides, and more."""
    from offline_rag.agent import run_agent

    async def _run() -> None:
        config, ollama, store, skill_registry = _runtime()
        chat_model = model or config.default_chat_model
        history_file = Path(config.data_dir) / "chat_history.json"

        messages: list[dict] = []
        if not fresh and history_file.exists():
            try:
                messages = json.loads(history_file.read_text(encoding="utf-8"))
                turns = sum(1 for m in messages if m.get("role") == "user")
                console.print(
                    f"[dim]Loaded {turns} previous turn(s). "
                    f"Type [bold]/fresh[/bold] to start over.[/dim]"
                )
            except Exception:
                messages = []

        console.print(
            f"\n[bold]Universal Bot[/bold]  "
            f"model=[cyan]{chat_model}[/cyan]  "
            f"[dim]tools: web · code · shell · files · slides · docs · skills[/dim]"
        )
        console.print(
            "[dim]Enter to send  ·  Shift+Enter for newline  ·  "
            "Ctrl+C to quit  ·  /fresh to clear history[/dim]\n"
        )

        try:
            while True:
                try:
                    user_input = console.input("[bold cyan]You:[/bold cyan] ").strip()
                except (EOFError, KeyboardInterrupt):
                    console.print("\n[dim]Goodbye.[/dim]")
                    break

                if not user_input:
                    continue
                if user_input.lower() in ("exit", "quit", "q"):
                    break
                if user_input.lower() == "/fresh":
                    messages = []
                    history_file.unlink(missing_ok=True)
                    console.print("[dim]Started fresh conversation.[/dim]\n")
                    continue

                messages.append({"role": "user", "content": user_input})
                console.print()
                console.print("[bold green]Bot:[/bold green] ", end="")

                parts: list[str] = []
                try:
                    async for chunk in run_agent(
                        messages, chat_model, config, ollama, store,
                        skill_registry=skill_registry,
                    ):
                        print(chunk, end="", flush=True)
                        parts.append(chunk)
                except Exception as exc:
                    err = f"\n❌ Error: {exc}"
                    print(err, end="", flush=True)
                    parts.append(err)

                print("\n")
                response = "".join(parts)
                messages.append({"role": "assistant", "content": response})

                # Persist conversation to disk
                history_file.parent.mkdir(parents=True, exist_ok=True)
                history_file.write_text(
                    json.dumps(messages, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

        finally:
            await ollama.aclose()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--host", default=None, help="Override config server_host")
@click.option("--port", "-p", default=None, type=int, help="Override config server_port")
def serve(host: str | None, port: int | None) -> None:
    """Start the Universal Bot API server (OpenAI-compatible)."""
    import uvicorn
    from offline_rag.config import load_config
    from offline_rag.server import app

    config = load_config()
    h = host or config.server_host
    p = port or config.server_port
    console.print(f"[bold]Universal Bot server[/bold] → [cyan]http://{h}:{p}[/cyan]")
    console.print(f"  API base : [cyan]http://{h}:{p}/v1[/cyan]")
    console.print(f"  Docs     : [cyan]http://{h}:{p}/docs[/cyan]")
    console.print(
        "\n  Expose to the internet: [dim]ngrok http {p}[/dim] "
        "then paste the URL into the web UI settings.\n"
    )
    uvicorn.run(app, host=h, port=p, log_level="warning")


# ---------------------------------------------------------------------------
# watch
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("paths", nargs=-1)
def watch(paths: tuple[str, ...]) -> None:
    """Index then watch paths for file changes (incremental re-index on save)."""

    async def _run() -> None:
        from offline_rag.indexer import FileIndexer
        from offline_rag.watcher import start_watching

        config, ollama, store, _ = _runtime()
        indexer = FileIndexer(config, store, ollama)

        watch_roots = (
            [str(Path(p).expanduser()) for p in paths]
            if paths
            else [str(Path(p).expanduser()) for p in config.paths]
        )

        console.print("[bold]Initial index...[/bold]")
        result = await indexer.index_paths(watch_roots)
        console.print(
            f"Indexed {result['indexed']} files. Watching {len(watch_roots)} path(s)..."
        )
        console.print("[dim]Press Ctrl+C to stop.[/dim]")

        try:
            await start_watching(watch_roots, indexer)
        finally:
            await ollama.aclose()

    asyncio.run(_run())
