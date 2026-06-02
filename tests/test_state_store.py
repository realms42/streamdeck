"""Tests for the thread-safe runtime state store."""

from __future__ import annotations

import threading

from streamdeck_service.state_store import StateStore


def test_get_returns_default_when_unset() -> None:
    s = StateStore()
    assert s.get("x") is None
    assert s.get("x", "fallback") == "fallback"


def test_set_and_get() -> None:
    s = StateStore()
    s.set("x", 42)
    assert s.get("x") == 42


def test_set_overwrites() -> None:
    s = StateStore()
    s.set("x", 1)
    s.set("x", 2)
    assert s.get("x") == 2


def test_set_same_value_does_not_fire_listener() -> None:
    s = StateStore()
    fired: list[str] = []
    s.add_listener(lambda k, v: fired.append(k))
    s.set("x", "hello")
    s.set("x", "hello")  # duplicate — should not fire
    assert fired == ["x"]


def test_listener_receives_key_and_value() -> None:
    s = StateStore()
    events: list[tuple[str, object]] = []
    s.add_listener(lambda k, v: events.append((k, v)))
    s.set("y", 99)
    assert events == [("y", 99)]


def test_multiple_listeners_all_fired() -> None:
    s = StateStore()
    counts: list[int] = [0, 0]
    s.add_listener(lambda k, v: counts.__setitem__(0, counts[0] + 1))
    s.add_listener(lambda k, v: counts.__setitem__(1, counts[1] + 1))
    s.set("z", True)
    assert counts == [1, 1]


def test_listener_exception_does_not_crash() -> None:
    s = StateStore()
    s.add_listener(lambda k, v: 1 / 0)
    s.set("x", 1)  # should not raise


def test_toggle_initialises_to_first_value() -> None:
    s = StateStore()
    s.toggle("x", ["a", "b"])
    assert s.get("x") == "a"


def test_toggle_cycles_forward() -> None:
    s = StateStore()
    s.set("x", "a")
    s.toggle("x", ["a", "b"])
    assert s.get("x") == "b"


def test_toggle_wraps_around() -> None:
    s = StateStore()
    s.set("x", "b")
    s.toggle("x", ["a", "b"])
    assert s.get("x") == "a"


def test_toggle_three_values() -> None:
    s = StateStore()
    vals = ["low", "mid", "high"]
    s.toggle("x", vals)
    assert s.get("x") == "low"
    s.toggle("x", vals)
    assert s.get("x") == "mid"
    s.toggle("x", vals)
    assert s.get("x") == "high"
    s.toggle("x", vals)
    assert s.get("x") == "low"


def test_snapshot_returns_copy() -> None:
    s = StateStore()
    s.set("a", 1)
    snap = s.snapshot()
    snap["a"] = 999
    assert s.get("a") == 1  # original unmodified


def test_prune_removes_unlisted_keys() -> None:
    s = StateStore()
    s.set("keep", 1)
    s.set("drop", 2)
    s.prune_to_keys({"keep"})
    assert s.get("keep") == 1
    assert s.get("drop") is None


def test_prune_empty_set_removes_all() -> None:
    s = StateStore()
    s.set("a", 1)
    s.set("b", 2)
    s.prune_to_keys(set())
    assert s.snapshot() == {}


def test_merge_copies_values() -> None:
    src = StateStore()
    src.set("x", 10)
    dst = StateStore()
    dst.merge(src)
    assert dst.get("x") == 10


def test_merge_does_not_fire_listeners() -> None:
    src = StateStore()
    src.set("x", 1)
    dst = StateStore()
    fired: list[str] = []
    dst.add_listener(lambda k, v: fired.append(k))
    dst.merge(src)
    assert fired == []


def test_thread_safety() -> None:
    """Concurrent writes should not corrupt the store."""
    s = StateStore()
    errors: list[Exception] = []

    def writer(key: str, val: int) -> None:
        try:
            for i in range(100):
                s.set(key, i + val)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(f"k{i}", i * 100)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
