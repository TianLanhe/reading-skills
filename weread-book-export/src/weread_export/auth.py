"""可复用认证快照和跨进程单登录窗口锁。"""

import asyncio
import fcntl
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import BinaryIO, Protocol


class AuthFactory(Protocol):
    async def is_authenticated(self, state: Mapping[str, object]) -> bool: ...

    async def capture_auth(self, progress=None) -> Mapping[str, object]: ...


class AuthStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, object] | None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        return payload if isinstance(payload, dict) else None

    def save_atomic(self, state: Mapping[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(dict(state), stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
            self.path.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)

    def reset(self) -> None:
        self.path.unlink(missing_ok=True)

    def status(self) -> dict[str, object]:
        state = self.load()
        return {"exists": state is not None, "path": str(self.path)}


class AsyncFileLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._stream: BinaryIO | None = None

    async def __aenter__(self) -> "AsyncFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        stream = self.path.open("a+b")
        self.path.chmod(0o600)
        await asyncio.to_thread(fcntl.flock, stream.fileno(), fcntl.LOCK_EX)
        self._stream = stream
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        if self._stream is None:
            return
        await asyncio.to_thread(fcntl.flock, self._stream.fileno(), fcntl.LOCK_UN)
        self._stream.close()
        self._stream = None


async def ensure_auth(
    factory: AuthFactory,
    store: AuthStore,
    lock: AsyncFileLock,
    progress=None,
) -> Mapping[str, object]:
    state = store.load()
    if state and await factory.is_authenticated(state):
        return state
    async with lock:
        state = store.load()
        if state and await factory.is_authenticated(state):
            return state
        state = dict(await factory.capture_auth(progress=progress))
        store.save_atomic(state)
        return state
