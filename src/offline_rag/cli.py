from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.text import Text

console = Console()
err_console = Console(stderr=True)


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet noisy third-party loggers
    for noisy in ("httpx", "httpcore", "watchdog", "lancedb", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _runtime():
    from offline_rag.config import load_config
    from offline_rag.ollama_client import OllamaClient
    from offline_rag.store import VectorStore

    config = load_config()
    ollama = OllamaClient(config.ollama_base_url)
    store = VectorStore(f"{config.data_dir}/lancedb")
    return config, ollama, store


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.option("--verbose", "-v", is_flag=True, default=False)
def cli(verbose: bool) -> None:
    """Offline RAG — local document intelligence powered by Ollama."""
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
    console.print("  [cyan]rag index ~/Documents[/cyan]")
    console.print("  [cyan]rag serve[/cyan]")


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("paths", nargs=-1)
def index(paths: tuple[str, ...]) -> None:
    """Index files. Pass paths or use config.yaml paths."""

    async def _run() -> dict:
        config, ollama, store = _runtime()
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
    from pathlib import Path

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
# chat
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--model", "-m", default=None, help="Ollama model name")
@click.option("--no-rag", is_flag=True, default=False, help="Disable retrieval")
def chat(model: str | None, no_rag: bool) -> None:
    """Interactive REPL with streaming responses and source citations."""

    async def _run() -> None:
        from offline_rag.retrieval import SYSTEM_PROMPT, retrieve

        config, ollama, store = _runtime()
        chat_model = model or config.default_chat_model

        console.print(f"[bold]Offline RAG Chat[/bold]  model=[cyan]{chat_model}[/cyan]")
        console.print(
            "Commands: [dim]exit[/dim] quit  |  [dim]?rag=off[/dim] disable retrieval for one turn\n"
        )

        history: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

        try:
            while True:
                try:
                    user_input = console.input("[bold cyan]You:[/bold cyan] ").strip()
                except (EOFError, KeyboardInterrupt):
                    break

                if not user_input:
                    continue
                if user_input.lower() in ("exit", "quit", "q"):
                    break

                turn_rag = not no_rag and "?rag=off" not in user_input
                clean = user_input.replace("?rag=off", "").strip()

                if turn_rag:
                    try:
                        result = await retrieve(clean, config, store, ollama)
                        if result.sources:
                            console.print("\n[dim]Sources:[/dim]")
                            for src in result.sources:
                                console.print(f"[dim]  • {src}[/dim]")
                            console.print()

                        if result.context_text:
                            augmented = (
                                f"Context:\n\n{result.context_text}\n\n---\n\nQuestion: {clean}"
                            )
                            history.append({"role": "user", "content": augmented})
                        else:
                            history.append({"role": "user", "content": clean})
                    except Exception as e:
                        console.print(f"[yellow]Retrieval warning: {e}[/yellow]")
                        history.append({"role": "user", "content": clean})
                else:
                    history.append({"role": "user", "content": clean})

                console.print("[bold green]Assistant:[/bold green] ", end="")
                parts: list[str] = []
                async for delta in ollama.chat_stream(chat_model, history):
                    print(delta, end="", flush=True)
                    parts.append(delta)
                print()

                history.append({"role": "assistant", "content": "".join(parts)})
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
    """Start the OpenAI-compatible RAG API server."""
    import uvicorn
    from offline_rag.config import load_config
    from offline_rag.server import app

    config = load_config()
    h = host or config.server_host
    p = port or config.server_port
    console.print(f"[bold]Starting RAG server[/bold] on [cyan]http://{h}:{p}[/cyan]")
    console.print(f"  OpenAI API base: [cyan]http://{h}:{p}/v1[/cyan]")
    uvicorn.run(app, host=h, port=p, log_level="warning")


# ---------------------------------------------------------------------------
# watch
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("paths", nargs=-1)
def watch(paths: tuple[str, ...]) -> None:
    """Index then watch for file changes (incremental re-index on save)."""

    async def _run() -> None:
        from offline_rag.indexer import FileIndexer
        from offline_rag.watcher import start_watching

        config, ollama, store = _runtime()
        indexer = FileIndexer(config, store, ollama)

        watch_roots = (
            [str(Path(p).expanduser()) for p in paths]
            if paths
            else [str(Path(p).expanduser()) for p in config.paths]
        )

        console.print("[bold]Initial index...[/bold]")
        result = await indexer.index_paths(watch_roots)
        console.print(f"Indexed {result['indexed']} files. Watching {len(watch_roots)} path(s)...")
        console.print("[dim]Press Ctrl+C to stop.[/dim]")

        try:
            await start_watching(watch_roots, indexer)
        finally:
            await ollama.aclose()

    asyncio.run(_run())
