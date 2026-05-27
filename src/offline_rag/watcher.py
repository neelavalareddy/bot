from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class _Handler:
    """Watchdog event handler that feeds an asyncio queue."""

    def __init__(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop) -> None:
        self._queue = queue
        self._loop = loop

    def _put(self, path: str) -> None:
        asyncio.run_coroutine_threadsafe(self._queue.put(path), self._loop)

    def dispatch(self, event) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        self._put(getattr(event, "src_path", ""))
        # Also handle moves
        dest = getattr(event, "dest_path", None)
        if dest:
            self._put(dest)


async def start_watching(paths: list, indexer) -> None:
    """Index then watch paths for changes, debouncing at 2 s."""
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        logger.error("watchdog not installed; cannot watch for changes")
        return

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[str] = asyncio.Queue()

    # Wrap our _Handler to satisfy watchdog's interface
    class _WatchdogAdapter(FileSystemEventHandler):
        def __init__(self, inner: _Handler) -> None:
            super().__init__()
            self._inner = inner

        def on_any_event(self, event):  # type: ignore[override]
            self._inner.dispatch(event)

    observer = Observer()
    handler = _WatchdogAdapter(_Handler(queue, loop))
    for p in paths:
        p = Path(p).expanduser()
        if p.exists():
            observer.schedule(handler, str(p), recursive=True)
            logger.info(f"Watching: {p}")

    observer.start()

    pending: set[str] = set()
    try:
        while True:
            try:
                path = await asyncio.wait_for(queue.get(), timeout=2.0)
                pending.add(path)
            except asyncio.TimeoutError:
                if pending:
                    logger.info(f"Re-indexing {len(pending)} changed path(s)...")
                    try:
                        await indexer.index_paths(list(pending))
                    except Exception as e:
                        logger.error(f"Incremental re-index failed: {e}")
                    pending.clear()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        observer.stop()
        observer.join()
