"""In-process parallelism for Ladybug/Kuzu backends sharing one ``Database``.

Ladybug is an embedded database: only one **read-write** ``Database`` object may
exist per file in a process, but multiple ``Connection``s created from that single
``Database`` may issue concurrent read *and* write transactions safely — the
transaction manager inside the shared ``Database`` serializes them correctly —
as long as the transactions touch **disjoint rows**. Two transactions writing the
*same* row raise a write-write conflict, so this primitive is for fan-out where
each worker owns distinct nodes/rows (e.g. one worker per document in
summarization), not for shared-row work.

Concurrent writes additionally require ``enable_multi_writes=True`` on the shared
``Database``; without it Ladybug rejects any second concurrent write transaction
with "Only one write transaction at a time is allowed".

This mirrors Ladybug's own ``AsyncConnection`` (shared ``Database`` + a pool of
``Connection``s + a ``ThreadPoolExecutor``), but yields plain **synchronous**
``KuzuBackend`` workers and a simple ordered ``map``. For genuine multi-*process*
access to one file, use Ladybug's REST API server instead — that is a different
problem and not what this solves.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, TypeVar

from genai_graph.kg.backend import KuzuBackend

T = TypeVar("T")
R = TypeVar("R")


class SharedKuzuParallel:
    """Run disjoint-row graph work concurrently against one shared Ladybug ``Database``.

    Opens a single ``ladybug.Database(path, enable_multi_writes=True)`` and a pool
    of ``max_workers`` ``KuzuBackend`` instances, each with its own ``Connection``
    (via ``KuzuBackend.attach``). ``map`` fans ``func`` out over ``items`` on a
    ``ThreadPoolExecutor``, lending each running task a backend from the pool.

    Use as a context manager so the shared ``Database`` and worker connections are
    always torn down:

    ```
    with SharedKuzuParallel(db_path, max_workers=4) as parallel:
        docs = list_documents(parallel.primary, folder_id=folder_id)
        results = parallel.map(summarize_one, [d["markdown_hash"] for d in docs])
    ```

    Args:
        db_path: Path to the on-disk ``.lbdb`` file (``:memory:`` only when the
            data already lives in that same in-memory ``Database``).
        max_workers: Number of worker backends / concurrent tasks. Must be >= 1.
        num_threads_per_query: Per-connection query-thread cap (0 = unlimited).

    Note:
        Workers must write **disjoint rows**; concurrent writes to the same row
        will conflict. Reads are always safe on any worker.
    """

    def __init__(self, db_path: str, *, max_workers: int = 4, num_threads_per_query: int = 0) -> None:
        if max_workers < 1:
            msg = f"max_workers must be >= 1, got {max_workers}"
            raise ValueError(msg)

        import ladybug

        self.db_path = db_path
        self.max_workers = max_workers
        self._db: Any = ladybug.Database(db_path, enable_multi_writes=True)
        self._workers: list[KuzuBackend] = [KuzuBackend() for _ in range(max_workers)]
        for w in self._workers:
            w.attach(self._db, num_threads=num_threads_per_query)
        self._pool: queue.Queue[KuzuBackend] = queue.Queue()
        for w in self._workers:
            self._pool.put(w)
        self._closed = False
        self._close_lock = threading.Lock()
        # First worker is the "primary", convenient for serial pre-reads
        # (list_documents, get_document) done by the caller before fan-out.
        self.primary: KuzuBackend = self._workers[0]

    def __enter__(self) -> "SharedKuzuParallel":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def map(self, func: Callable[[KuzuBackend, T], R], items: list[T]) -> list[R | Exception]:
        """Apply ``func(worker_backend, item)`` over ``items``, concurrently and in order.

        Each running task borrows a backend from the pool and returns it on completion,
        so at most ``max_workers`` items are processed at once. The returned list is
        aligned with ``items``. If a worker raises, its slot holds the ``Exception``
        instance instead of a result — one bad item does not abort the run.

        Args:
            func: Callable taking a borrowed ``KuzuBackend`` and one item.
            items: Items to fan out over.

        Returns:
            One entry per item, in input order: either ``func``'s return value or
            the ``Exception`` it raised.
        """
        results: list[R | Exception] = [None] * len(items)  # type: ignore[list-item]

        def _run(index: int, item: T) -> None:
            backend = self._pool.get()  # blocks only if all workers are busy
            try:
                results[index] = func(backend, item)
            except Exception as exc:  # noqa: BLE001 - returned as a value, per contract
                results[index] = exc
            finally:
                self._pool.put(backend)

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [executor.submit(_run, i, item) for i, item in enumerate(items)]
            for fut in as_completed(futures):
                fut.result()  # surface BaseException (e.g. KeyboardInterrupt); _run swallows Exception
        return results

    def close(self) -> None:
        """Close every worker connection and the shared ``Database`` (idempotent)."""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            for w in self._workers:
                try:
                    if w.conn is not None:
                        w.conn.close()
                except Exception:  # noqa: BLE001 - best-effort teardown
                    pass
                w.db = None
                w.conn = None
            try:
                self._db.close()
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass
