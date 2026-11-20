"""Small JSON file of what already happened: messages sent, resets run, Keep/Release answers.

Every value is a timestamp, so old entries can be dropped after a while. The web endpoint and
the scheduler share one State, so every change is a locked read-modify-write.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

KEEP_DAYS = 60


def stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


class State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def read(self) -> dict[str, dict[str, str]]:
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (FileNotFoundError, ValueError):
                return {}
            return {k: v for k, v in data.items() if isinstance(v, dict)}

    def section(self, name: str) -> dict[str, str]:
        return self.read().get(name, {})

    def update(self, fn: Callable[[dict[str, dict[str, str]]], None]) -> None:
        with self._lock:
            data = self.read()
            fn(data)
            cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).isoformat()
            data = {name: {k: v for k, v in entries.items() if v >= cutoff}
                    for name, entries in data.items()}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)

    def mark(self, name: str, *keys: str) -> None:
        now = stamp()
        self.update(lambda d: d.setdefault(name, {}).update(dict.fromkeys(keys, now)))

    def forget(self, name: str, *keys: str) -> None:
        def drop(d):
            for k in keys:
                d.get(name, {}).pop(k, None)
        self.update(drop)
