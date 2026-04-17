"""watchdog daemon — keeps brain_raw and brain_wiki in sync with the filesystem."""

from __future__ import annotations

import logging
import logging.handlers
import signal
import sys
import threading
import time
from pathlib import Path
from queue import Queue

from watchdog.events import (
    FileDeletedEvent,
    FileMovedEvent,
    PatternMatchingEventHandler,
)
from watchdog.observers import Observer

from . import config, embed as embed_mod, store

log = logging.getLogger("brain-watch")


def _setup_logging() -> None:
    config.SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    rot = logging.handlers.RotatingFileHandler(
        config.LOG_PATH, maxBytes=10_000_000, backupCount=5
    )
    rot.setFormatter(fmt)
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[rot, stream])


IGNORED_PARTS = {".chroma", ".venv", ".obsidian", "assets", "__pycache__"}


def _should_ignore(path: Path) -> bool:
    if path.name.startswith("."):
        return True
    if path.suffix.lower() != ".md":
        return True
    if path.name.endswith(("~", ".tmp")):
        return True
    for part in path.parts:
        if part in IGNORED_PARTS:
            return True
    return False


class DebouncedDispatcher:
    """Per-path timer; coalesces rapid events into one worker submission."""

    def __init__(self, worker_queue: Queue, debounce_seconds: float):
        self.queue = worker_queue
        self.debounce = debounce_seconds
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, path: Path, collection: str) -> None:
        key = f"{kind}:{path}"
        with self._lock:
            t = self._timers.pop(key, None)
            if t:
                t.cancel()
            new_timer = threading.Timer(
                self.debounce, self._fire, args=(kind, path, collection, key)
            )
            self._timers[key] = new_timer
            new_timer.daemon = True
            new_timer.start()

    def _fire(self, kind: str, path: Path, collection: str, key: str) -> None:
        with self._lock:
            self._timers.pop(key, None)
        self.queue.put((kind, path, collection))


class BrainHandler(PatternMatchingEventHandler):
    def __init__(self, dispatcher: DebouncedDispatcher, collection: str, allowed_names: set[str] | None = None):
        super().__init__(patterns=["*.md"], ignore_directories=True)
        self.dispatcher = dispatcher
        self.collection = collection
        self.allowed_names = allowed_names  # None = any .md in dir

    def _eligible(self, path: Path) -> bool:
        if _should_ignore(path):
            return False
        if self.allowed_names is not None and path.name not in self.allowed_names:
            return False
        return True

    def on_created(self, event):
        p = Path(event.src_path)
        if self._eligible(p):
            self.dispatcher.submit("embed", p, self.collection)

    def on_modified(self, event):
        p = Path(event.src_path)
        if self._eligible(p):
            self.dispatcher.submit("embed", p, self.collection)

    def on_moved(self, event: FileMovedEvent):
        src = Path(event.src_path)
        dest = Path(event.dest_path)
        if self._eligible(src):
            self.dispatcher.submit("delete", src, self.collection)
        if self._eligible(dest):
            self.dispatcher.submit("embed", dest, self.collection)

    def on_deleted(self, event: FileDeletedEvent):
        p = Path(event.src_path)
        if self._eligible(p):
            self.dispatcher.submit("delete", p, self.collection)


_stop = threading.Event()


def _worker(queue: Queue) -> None:
    while not _stop.is_set():
        try:
            item = queue.get(timeout=0.5)
        except Exception:
            continue
        if item is None:
            break
        kind, path, collection = item
        try:
            if kind == "embed":
                if not path.exists():
                    log.info("SKIP gone-before-embed %s", path)
                    continue
                r = embed_mod.embed_file(path, collection)
                status = r.get("status")
                if status == "embedded":
                    log.info(
                        "EMBED %s %s chunks=%d model=%s",
                        collection,
                        r.get("source_rel"),
                        r.get("chunks", 0),
                        r.get("embedding_model"),
                    )
                elif status == "unchanged":
                    log.info("UNCHANGED %s %s chunks=%d", collection, r.get("source_rel"), r.get("chunks", 0))
                elif status == "skipped":
                    log.info("SKIP %s %s reason=%s", collection, r.get("source_rel") or path.name, r.get("reason"))
                else:
                    log.info("RESULT %s %s", collection, r)
            elif kind == "delete":
                store.delete_by_source(collection, str(path.resolve()))
                log.info("DELETE %s %s", collection, path)
        except Exception as e:
            log.exception("ERROR handling %s %s: %s", kind, path, e)
        finally:
            queue.task_done()


def _signal(*_):
    log.info("shutdown signal received")
    _stop.set()


def main() -> None:
    _setup_logging()
    config.validate_or_exit()
    log.info("brain-watch starting; chroma=%s model=%s provider=%s", config.CHROMA_DIR, config.EMBED_MODEL, config.EMBED_PROVIDER)

    queue: Queue = Queue()
    dispatcher = DebouncedDispatcher(queue, config.WATCH_DEBOUNCE_SECONDS)

    observer = Observer()

    raw_handler = BrainHandler(dispatcher, config.COLL_RAW)
    observer.schedule(raw_handler, str(config.RAW_DIR), recursive=False)

    pages_handler = BrainHandler(dispatcher, config.COLL_WIKI)
    observer.schedule(pages_handler, str(config.WIKI_PAGES), recursive=False)

    top_handler = BrainHandler(dispatcher, config.COLL_WIKI, allowed_names={"index.md", "overview.md"})
    observer.schedule(top_handler, str(config.WIKI_TOP), recursive=False)

    signal.signal(signal.SIGTERM, _signal)
    signal.signal(signal.SIGINT, _signal)

    worker_thread = threading.Thread(target=_worker, args=(queue,), daemon=True)
    worker_thread.start()

    observer.start()
    try:
        while not _stop.is_set():
            time.sleep(0.5)
    finally:
        log.info("stopping observer")
        observer.stop()
        observer.join(timeout=5)
        queue.put(None)
        worker_thread.join(timeout=5)
        log.info("brain-watch stopped")


if __name__ == "__main__":
    main()
