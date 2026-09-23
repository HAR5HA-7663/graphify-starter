"""Warm query server: holds chromadb, the HNSW index and the tokenizer in memory.

Started on demand by query_client.spawn(); one per machine (flock on config.QUERY_LOCK).
Serves one JSON line per connection over config.QUERY_SOCKET (mode 600). Exits after
config.QUERY_DAEMON_IDLE_S idle, or when a client's fingerprint differs from the one it
started with (edited code, .env, or BRAIN_* env vars) so it never answers with stale
settings. Chroma keeps the HNSW index in process memory, so writes by the watcher are
invisible here until the store is reopened — done whenever .chroma changes on disk.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import socket
import sys
import time
import traceback

from . import config, query_client

log = logging.getLogger("query_daemon")


def _store_stamp() -> tuple:
    stamps = []
    for root, _dirs, files in os.walk(config.CHROMA_DIR):
        for name in files:
            try:
                st = os.stat(os.path.join(root, name))
            except OSError:
                continue
            stamps.append((name, st.st_mtime_ns, st.st_size))
    return tuple(sorted(stamps))


def _open_store():
    from . import query, store

    for name in config.ALLOWED_COLLECTIONS:
        query._warm_index(store, name)  # noqa: SLF001
    return _store_stamp()


def _reopen_store():
    from chromadb.api.client import SharedSystemClient

    from . import store

    store._client = None  # noqa: SLF001
    SharedSystemClient.clear_system_cache()
    return _open_store()


def serve() -> int:
    lock = open(config.QUERY_LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0  # another daemon is already up

    from . import chunker, query  # noqa: F401 — the imports being kept warm

    config.validate_or_exit()
    started_fp = query_client.fingerprint()
    stamp = _open_store()

    try:
        config.QUERY_SOCKET.unlink()
    except FileNotFoundError:
        pass
    old_umask = os.umask(0o077)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(config.QUERY_SOCKET))
    os.umask(old_umask)
    server.listen(8)
    server.settimeout(config.QUERY_DAEMON_IDLE_S)
    log.info("up pid=%s fp=%s", os.getpid(), started_fp)

    try:
        while True:
            try:
                conn, _ = server.accept()
            except socket.timeout:
                log.info("idle %ss, exiting", config.QUERY_DAEMON_IDLE_S)
                return 0
            with conn:
                conn.settimeout(5.0)
                try:
                    msg = json.loads(conn.makefile("rb").readline())
                except (OSError, ValueError):
                    continue
                if msg.get("fingerprint") != started_fp:
                    conn.sendall(b'{"restart": true}\n')
                    log.info("fingerprint changed, exiting so the next query starts a fresh daemon")
                    return 0
                t0 = time.perf_counter()
                try:
                    current = _store_stamp()
                    if current != stamp:
                        stamp = _reopen_store()
                        log.info("store changed on disk, reopened")
                    payload = query.run(**msg["request"])
                    payload["served_by"] = "daemon"
                    reply = {"payload": payload}
                except SystemExit:
                    reply = {"error": "validation failed"}
                except Exception as e:  # noqa: BLE001 — the client falls back in-process
                    log.error("query failed: %s\n%s", e, traceback.format_exc())
                    reply = {"error": str(e)}
                conn.settimeout(5.0)
                try:
                    conn.sendall(json.dumps(reply).encode() + b"\n")
                except OSError:
                    pass
                log.debug("served in %d ms", (time.perf_counter() - t0) * 1000)
    finally:
        server.close()
        try:
            config.QUERY_SOCKET.unlink()  # safe: nobody else can bind while we hold the lock
        except OSError:
            pass


if __name__ == "__main__":
    # Only this module's lifecycle lines: chromadb's broken-telemetry errors, its
    # WAL-replay warnings and httpx's per-request lines would grow the log every query.
    logging.basicConfig(
        stream=sys.stderr, level=logging.CRITICAL, format="%(asctime)s %(levelname)s %(message)s"
    )
    log.setLevel(logging.INFO)
    sys.exit(serve())
