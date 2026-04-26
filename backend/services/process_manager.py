from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from collections import deque
from typing import AsyncIterator, Optional

import psutil

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _port_in_use_by_other(port: int, our_pid: Optional[int]) -> Optional[int]:
    try:
        for c in psutil.net_connections(kind="inet"):
            laddr = getattr(c, "laddr", None)
            if not laddr or getattr(laddr, "port", None) != port:
                continue
            if c.status != psutil.CONN_LISTEN:
                continue
            pid = c.pid
            if pid is None:
                continue
            if our_pid is not None and pid == our_pid:
                continue
            return pid
    except (psutil.AccessDenied, OSError):
        return None
    return None


class _LogBuffer:
    def __init__(self, capacity: int = 500):
        self._buf: deque[str] = deque(maxlen=capacity)
        self._subs: set[asyncio.Queue[str]] = set()
        self._lock = asyncio.Lock()

    async def push(self, line: str) -> None:
        async with self._lock:
            self._buf.append(line)
            for q in list(self._subs):
                if q.full():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                q.put_nowait(line)

    async def subscribe(self) -> tuple[list[str], asyncio.Queue[str]]:
        async with self._lock:
            history = list(self._buf)
            q: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
            self._subs.add(q)
            return history, q

    async def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        async with self._lock:
            self._subs.discard(q)


class ManagedProcess:
    def __init__(
        self,
        name: str,
        python_module: str,
        port: int,
        extra_env: Optional[dict[str, str]] = None,
        log_capacity: int = 500,
    ) -> None:
        self.name = name
        self.module = python_module
        self.port = port
        self.extra_env = dict(extra_env or {})
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._started_at: Optional[float] = None
        self._logs = _LogBuffer(capacity=log_capacity)

    def status(self) -> str:
        if self._proc is not None and self._proc.returncode is None:
            return "running"
        external_pid = _port_in_use_by_other(self.port, our_pid=None)
        if external_pid is not None:
            return "external"
        return "offline"

    def info(self) -> dict:
        st = self.status()
        return {
            "name": self.name,
            "port": self.port,
            "status": st,
            "pid": self._proc.pid if (self._proc and self._proc.returncode is None) else None,
            "started_at": self._started_at,
        }

    async def start(self) -> dict:
        if self.status() == "running":
            return self.info()
        if self.status() == "external":
            raise RuntimeError(f"port {self.port} occupied by external process")

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env.update(self.extra_env)

        cmd = [
            sys.executable, "-m", "uvicorn",
            f"{self.module}:app",
            "--host", "127.0.0.1",
            "--port", str(self.port),
            "--log-level", "info",
        ]
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        self._started_at = time.time()
        self._reader_task = asyncio.create_task(self._read_stdout())
        return self.info()

    async def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        stream = self._proc.stdout
        while True:
            raw = await stream.readline()
            if not raw:
                break
            try:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            except Exception:
                continue
            await self._logs.push(_strip_ansi(line))

    async def stop(self, timeout: float = 3.0, force_external: bool = False) -> dict:
        if self._proc is not None and self._proc.returncode is None:
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass
                await self._proc.wait()

        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None

        self._proc = None
        self._started_at = None

        if force_external:
            ext_pid = _port_in_use_by_other(self.port, our_pid=None)
            if ext_pid is not None:
                try:
                    p = psutil.Process(ext_pid)
                    p.terminate()
                    try:
                        p.wait(timeout=timeout)
                    except psutil.TimeoutExpired:
                        p.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

        return self.info()

    async def logs_stream(self) -> AsyncIterator[str]:
        history, q = await self._logs.subscribe()
        try:
            for line in history:
                yield line
            while True:
                line = await q.get()
                yield line
        finally:
            await self._logs.unsubscribe(q)


class ProcessRegistry:
    def __init__(self) -> None:
        self._procs: dict[str, ManagedProcess] = {}

    def register(self, mp: ManagedProcess) -> ManagedProcess:
        self._procs[mp.name] = mp
        return mp

    def get(self, name: str) -> Optional[ManagedProcess]:
        return self._procs.get(name)

    def all(self) -> list[ManagedProcess]:
        return list(self._procs.values())

    async def stop_all(self) -> None:
        await asyncio.gather(*(mp.stop() for mp in self._procs.values()), return_exceptions=True)


registry = ProcessRegistry()
