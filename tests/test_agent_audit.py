import dataclasses
from pathlib import Path

from lexrh.agent.audit import FileAuditLog, MemoryAuditLog, hash_args, verify_chain


def test_entries_are_chained_and_verify() -> None:
    log = MemoryAuditLog()
    log.record("c1", "agent", "a", tool="t", args={"x": 1}, result="ok")
    log.record("c1", "human:bob", "b")
    assert log.verify()
    first, second = log.entries()
    assert second.prev_hash == first.hash


def test_arguments_are_hashed_not_stored() -> None:
    log = MemoryAuditLog()
    entry = log.record("c1", "agent", "tool_call", args={"name": "Camille Test"})
    assert entry.args_hash == hash_args({"name": "Camille Test"})
    assert "Camille" not in repr(entry)


def test_editing_an_entry_breaks_the_chain() -> None:
    log = MemoryAuditLog()
    log.record("c1", "agent", "a", result="one")
    log.record("c1", "agent", "b", result="two")
    entries = log.entries()
    forged = [dataclasses.replace(entries[0], result="changed"), entries[1]]
    assert not verify_chain(forged)


def test_removing_an_entry_breaks_the_chain() -> None:
    log = MemoryAuditLog()
    for action in "abc":
        log.record("c1", "agent", action)
    entries = log.entries()
    assert not verify_chain([entries[0], entries[2]])


def test_file_log_is_append_only_and_reloads(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    FileAuditLog(path).record("c1", "agent", "first")
    reopened = FileAuditLog(path)
    reopened.record("c1", "agent", "second")
    assert [e.action for e in FileAuditLog(path).entries()] == ["first", "second"]
    assert FileAuditLog(path).verify()


def test_entries_can_be_filtered_by_case() -> None:
    log = MemoryAuditLog()
    log.record("c1", "agent", "a")
    log.record("c2", "agent", "b")
    assert [e.action for e in log.entries("c2")] == ["b"]
