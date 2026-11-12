"""Append-only, tamper-evident audit log.

Every step of a case writes one entry: who did what, with which tool, a hash of the arguments
(never the arguments themselves, which may hold personal data) and a short result summary.
Each entry carries the hash of the previous one, so editing or removing an entry breaks the
chain and ``verify()`` reports it.

Two stores: ``MemoryAuditLog`` (tests) and ``FileAuditLog`` (JSON lines, opened in append
mode only). A PostgreSQL table with no UPDATE/DELETE grant is the production version of the
same interface.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

GENESIS = "0" * 64


@dataclass(frozen=True)
class Entry:
    ts: str
    case_id: str
    actor: str  # "agent" or "human:<id>"
    action: str
    tool: str | None
    args_hash: str | None
    result: str
    prev_hash: str
    hash: str


def hash_args(args: dict[str, Any]) -> str:
    canonical = json.dumps(args, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _entry_hash(fields: dict[str, Any]) -> str:
    canonical = json.dumps(fields, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


class AuditLog(Protocol):
    def record(
        self,
        case_id: str,
        actor: str,
        action: str,
        *,
        tool: str | None = None,
        args: dict[str, Any] | None = None,
        result: str = "",
    ) -> Entry: ...

    def entries(self, case_id: str | None = None) -> list[Entry]: ...

    def verify(self) -> bool: ...


class _ChainedLog:
    """Shared chaining logic; subclasses only say where entries are stored."""

    def __init__(self) -> None:
        self._entries: list[Entry] = []

    def _persist(self, entry: Entry) -> None:
        raise NotImplementedError

    def record(
        self,
        case_id: str,
        actor: str,
        action: str,
        *,
        tool: str | None = None,
        args: dict[str, Any] | None = None,
        result: str = "",
    ) -> Entry:
        prev = self._entries[-1].hash if self._entries else GENESIS
        fields: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "case_id": case_id,
            "actor": actor,
            "action": action,
            "tool": tool,
            "args_hash": hash_args(args) if args is not None else None,
            "result": result[:300],
            "prev_hash": prev,
        }
        entry = Entry(**fields, hash=_entry_hash(fields))
        self._persist(entry)
        self._entries.append(entry)
        return entry

    def entries(self, case_id: str | None = None) -> list[Entry]:
        return [e for e in self._entries if case_id is None or e.case_id == case_id]

    def verify(self) -> bool:
        return verify_chain(self._entries)


def verify_chain(entries: list[Entry]) -> bool:
    prev = GENESIS
    for entry in entries:
        fields = asdict(entry)
        claimed = fields.pop("hash")
        if entry.prev_hash != prev or _entry_hash(fields) != claimed:
            return False
        prev = claimed
    return True


class MemoryAuditLog(_ChainedLog):
    def _persist(self, entry: Entry) -> None:
        return None


class FileAuditLog(_ChainedLog):
    """JSON lines file opened in append mode only. Existing entries are loaded on start."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        if path.exists():
            self._entries = [Entry(**json.loads(line)) for line in path.read_text().splitlines()]

    def _persist(self, entry: Entry) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
