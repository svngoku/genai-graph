"""Unit tests for the ``SharedKuzuParallel`` in-process concurrency primitive.

Uses a throwaway on-disk Ladybug database (no mocks for the DB) and a disjoint
write pattern that mirrors the summarize path (each worker owns its own rows).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import ladybug
import pytest

from genai_graph.kg.backend import KuzuBackend
from genai_graph.kg.parallel import SharedKuzuParallel


def _make_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "parallel.lbdb")
    db = ladybug.Database(db_path)
    conn = ladybug.Connection(db)
    conn.execute("CREATE NODE TABLE Probe(id STRING, n INT64, thread INT64, PRIMARY KEY(id));")
    conn.close()
    db.close()
    return db_path


def _write_one(backend: KuzuBackend, payload: tuple[int, int]) -> int:
    tid, i = payload
    backend.execute(
        "MERGE (p:Probe {id: $id}) SET p.n = $n, p.thread = $t;",
        {"id": f"t{tid}-{i}", "n": i, "t": tid},
    )
    time.sleep(0.02)  # I/O-bound gap outside the txn, like an LLM call
    return i


def _count(db_path: str) -> dict[str, tuple[int, int]]:
    db = ladybug.Database(db_path, read_only=True)
    conn = ladybug.Connection(db)
    rows = conn.execute("MATCH (p:Probe) RETURN p.id AS id, p.n AS n, p.thread AS t;")
    seen: dict[str, tuple[int, int]] = {}
    while rows.has_next():
        r = rows.get_next()  # positional: [id, n, thread]
        seen[r[0]] = (int(r[1]), int(r[2]))
    conn.close()
    db.close()
    return seen


@pytest.mark.integration
def test_disjoint_concurrent_writes_all_persist(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    items = [(tid, i) for tid in range(4) for i in range(15)]

    with SharedKuzuParallel(db_path, max_workers=4) as parallel:
        results = parallel.map(_write_one, items)
        worker_conns = {id(w.conn) for w in parallel._workers}

    assert len(results) == len(items)
    assert all(r == i for (_, i), r in zip(items, results, strict=True))  # no exceptions returned
    # Each worker has its own Connection object (no shared connection).
    assert len(worker_conns) == 4

    seen = _count(db_path)
    expected = {f"t{tid}-{i}": (i, tid) for tid, i in items}
    assert seen == expected  # full integrity, no lost writes


@pytest.mark.integration
def test_map_preserves_input_order(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    items = [(tid, i) for tid in range(2) for i in range(8)]

    with SharedKuzuParallel(db_path, max_workers=4) as parallel:
        results = parallel.map(_write_one, items)

    assert results == [i for (_, i) in items]


@pytest.mark.integration
def test_per_item_exception_returned_not_raised(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)

    def _boom(backend: KuzuBackend, item: int) -> int:
        if item == 2:
            raise ValueError("boom on 2")
        return item * 10

    with SharedKuzuParallel(db_path, max_workers=2) as parallel:
        results = parallel.map(_boom, [0, 1, 2, 3])

    assert results[0] == 0
    assert results[1] == 10
    assert isinstance(results[2], ValueError)
    assert str(results[2]) == "boom on 2"
    assert results[3] == 30


@pytest.mark.integration
def test_workers_actually_run_in_parallel(tmp_path: Path) -> None:
    db_path = _make_db(tmp_path)
    barrier = threading.Barrier(4)
    held: list[float] = []

    def _hold(backend: KuzuBackend, item: int) -> float:
        # All four workers must reach the barrier simultaneously to proceed —
        # proves they are running concurrently, not serialized.
        barrier.wait(timeout=5)
        t = time.monotonic()
        held.append(t)
        backend.execute("MERGE (p:Probe {id: $id}) SET p.n = $n;", {"id": f"h{item}", "n": item})
        return t

    with SharedKuzuParallel(db_path, max_workers=4) as parallel:
        times = parallel.map(_hold, range(4))

    # All four workers were alive at the same instant (barrier released), and
    # they completed within a tight window — not spread over 4x the hold time.
    assert len(times) == 4
    assert max(times) - min(times) < 1.0


def test_max_workers_below_one_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_workers must be >= 1"):
        SharedKuzuParallel(str(tmp_path / "x.lbdb"), max_workers=0)
