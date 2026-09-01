from __future__ import annotations

import copy
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable


EMPTY_STATE = {
    "schema_version": "1.0.0",
    "projects": {},
    "assets": {},
    "preset_selections": {},
    "generation_requests": {},
    "runs": {},
    "artifacts": {},
    "artifact_reviews": {},
    "scenarios": {},
    "scenario_selections": {},
}


class LocalStore:
    """Small transactional JSON store for a single local platform process."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self._lock = threading.RLock()
        if not self.state_path.exists():
            self._write(copy.deepcopy(EMPTY_STATE))

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"platform state is unreadable: {exc}") from exc
        for key, default in EMPTY_STATE.items():
            data.setdefault(key, copy.deepcopy(default))
        return data

    def _write(self, data: dict[str, Any]) -> None:
        temp = self.root / f".{self.state_path.name}.{uuid.uuid4().hex}.tmp"
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        for attempt in range(8):
            try:
                os.replace(temp, self.state_path)
                return
            except PermissionError:
                if attempt == 7:
                    temp.unlink(missing_ok=True)
                    raise
                time.sleep(0.01 * (attempt + 1))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._read())

    def transaction(self, operation: Callable[[dict[str, Any]], Any]) -> Any:
        with self._lock:
            state = self._read()
            result = operation(state)
            self._write(state)
            return copy.deepcopy(result)

    def insert(self, collection: str, record: dict[str, Any]) -> dict[str, Any]:
        def operation(state: dict[str, Any]) -> dict[str, Any]:
            state[collection][record["id"]] = copy.deepcopy(record)
            return record

        return self.transaction(operation)

    def get(self, collection: str, record_id: str) -> dict[str, Any] | None:
        with self._lock:
            return copy.deepcopy(self._read()[collection].get(record_id))

    def list(self, collection: str, predicate: Callable[[dict[str, Any]], bool] | None = None) -> list[dict[str, Any]]:
        with self._lock:
            values = list(self._read()[collection].values())
        if predicate:
            values = [value for value in values if predicate(value)]
        return copy.deepcopy(sorted(values, key=lambda item: item.get("created_at", ""), reverse=True))

    def update(self, collection: str, record_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
        def operation(state: dict[str, Any]) -> dict[str, Any] | None:
            record = state[collection].get(record_id)
            if record is None:
                return None
            record.update(copy.deepcopy(values))
            return record

        return self.transaction(operation)

    def delete(self, collection: str, record_id: str) -> dict[str, Any] | None:
        def operation(state: dict[str, Any]) -> dict[str, Any] | None:
            return state[collection].pop(record_id, None)

        return self.transaction(operation)

    def append_run_log(self, run_id: str, entry: dict[str, Any]) -> dict[str, Any] | None:
        def operation(state: dict[str, Any]) -> dict[str, Any] | None:
            record = state["runs"].get(run_id)
            if record is None:
                return None
            record.setdefault("logs", []).append(copy.deepcopy(entry))
            return record

        return self.transaction(operation)

    def project_dir(self, project_id: str) -> Path:
        path = (self.root / "projects" / project_id).resolve()
        if self.root not in path.parents:
            raise ValueError("invalid project path")
        path.mkdir(parents=True, exist_ok=True)
        return path
