"""
One run at a time per database: the lock file next to data.db (data.db.lock).

A long corpus is processed by a chain of batch jobs, each resuming where the last stopped.
Two of them on the same database at once (a resubmission that starts while the first still
runs, an interactive run during a job) would send the same notes twice and, on Argon's
NFSv3 scratch where SQLite's own locking cannot be relied on across nodes, risk corrupting
the file. So every run that writes takes this lock first, and --clean refuses while another
run holds it.

The lock is a file created with O_EXCL, holding who took it (host, pid, SGE job id, start
time and a random token). While held, a background thread refreshes its modification time
every BEAT_SECONDS. A process killed outright (SGE's h_rt limit sends SIGKILL) cannot remove
it, so a lock whose refresh is older than STALE_SECONDS, or whose owner is a dead process on
this host, is stale and taken over. A run that finds a fresh lock waits up to STALE_SECONDS
for it to go stale (a job chained right after a killed one starts within that window), then
gives up. The holder checks before each note that the lock is still its own (check()), so a
run suspended long enough to lose it (SGE suspends with SIGSTOP) stops instead of writing
alongside the run that took over.
"""

import json
import os
import secrets
import socket
import threading
import time
from pathlib import Path

BEAT_SECONDS = 30
STALE_SECONDS = 180
WAIT_POLL_SECONDS = 5


class LockHeld(Exception):
    """Another live run holds the lock."""


def lock_path(db_path: Path) -> Path:
    return db_path.with_name(db_path.name + ".lock")


def _owner_now() -> dict:
    return {
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "job": os.environ.get("JOB_ID") or None,
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        "token": secrets.token_hex(8),
    }


def _read(path: Path) -> tuple[dict | None, float | None]:
    """(owner, seconds since the last refresh) of the lock at path, or (None, None) when there is none."""
    try:
        age = time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return None, None
    try:
        owner = json.loads(path.read_text() or "{}")
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError):
        owner = {}   # unreadable or half written: judged by its age alone
    return owner, age


def describe(owner: dict | None, age: float | None) -> str:
    """The lock's owner in one line, for logs and errors."""
    if not owner:
        return "an unreadable lock file"
    job = f", SGE job {owner['job']}" if owner.get("job") else ""
    return (
        f"pid {owner.get('pid')} on {owner.get('host')}{job}, started {owner.get('started')}, "
        f"refreshed {age:.0f}s ago"
    )


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, TypeError, ValueError):
        return True   # exists under another user, or unknown: assume alive
    return True


def stale_reason(owner: dict | None, age: float | None) -> str | None:
    """Why the lock is stale (safe to take over), or None while its owner may still be running."""
    if age is None:
        return None
    if age > STALE_SECONDS:
        return f"not refreshed for {age:.0f}s (owner presumably killed)"
    if owner and owner.get("host") == socket.gethostname() and not _pid_alive(owner.get("pid")):
        return f"its owner, pid {owner.get('pid')} on this host, is no longer running"
    return None


def status(db_path: Path) -> str | None:
    """A line about the lock for the status report, or None when there is none."""
    owner, age = _read(lock_path(db_path))
    if age is None:
        return None
    stale = stale_reason(owner, age)
    if stale:
        return f"stale lock from {describe(owner, age)}: {stale}; the next run takes it over"
    return f"HELD by a running pipeline: {describe(owner, age)}"


class RunLock:
    """The lock on one database; acquire() before writing, release() when done (or use with)."""

    def __init__(self, db_path: Path, report=print):
        self.path = lock_path(db_path)
        self.report = report
        self.owner = None
        self._stop = threading.Event()
        self._thread = None

    def acquire(self, wait_seconds: float = STALE_SECONDS + BEAT_SECONDS):
        """Takes the lock, taking over a stale one; raises LockHeld when a live run keeps it fresh for wait_seconds."""
        deadline = time.monotonic() + wait_seconds
        announced = False
        while True:
            owner = _owner_now()
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "w") as f:
                    json.dump(owner, f)
                    f.flush()
                    os.fsync(f.fileno())
                self.owner = owner
                self._thread = threading.Thread(target=self._beat, name="run-lock", daemon=True)
                self._thread.start()
                return

            held, age = _read(self.path)
            if age is None:
                continue   # released between our attempt and the read: try again at once
            stale = stale_reason(held, age)
            if stale:
                self.report(f"Taking over the run lock {self.path}: {describe(held, age)}; {stale}.")
                self._break(held)
                continue
            if time.monotonic() >= deadline:
                raise LockHeld(
                    f"another run is using this database ({describe(held, age)}; lock file {self.path}). "
                    f"Wait for it to finish, or stop it. If you are certain no run is active, delete {self.path}."
                )
            if not announced:
                self.report(
                    f"Waiting for the run lock {self.path}, held by {describe(held, age)}; it is taken over once "
                    f"its owner stops refreshing it for {STALE_SECONDS}s (at most {wait_seconds:.0f}s of waiting)."
                )
                announced = True
            time.sleep(WAIT_POLL_SECONDS)

    def _break(self, judged: dict | None):
        """Removes a stale lock, unless it changed hands since it was judged stale (another run took it over first)."""
        aside = self.path.with_name(f"{self.path.name}.stale-{socket.gethostname()}-{os.getpid()}")
        try:
            os.rename(self.path, aside)
        except FileNotFoundError:
            return
        moved, _ = _read(aside)
        if judged and moved and moved.get("token") != judged.get("token"):
            # A fresh lock from a run that broke the stale one first: put it back, unless the
            # path is taken again already, and let the loop judge it afresh.
            try:
                os.link(aside, self.path)
            except FileExistsError:
                pass
        aside.unlink(missing_ok=True)

    def _beat(self):
        while not self._stop.wait(BEAT_SECONDS):
            try:
                now = time.time()
                os.utime(self.path, (now, now))
            except OSError:
                pass   # lost (check() says so) or a transient NFS error (the next beat retries)

    def check(self) -> str | None:
        """None while the lock is still this run's; otherwise why it is not (the run must stop writing)."""
        held, age = _read(self.path)
        if held is None:
            return f"the run lock {self.path} was removed while this run held it"
        if held.get("token") != self.owner["token"]:
            return f"the run lock {self.path} was taken over by {describe(held, age)} (this run was paused too long?)"
        return None

    def release(self):
        if self.owner is None:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self.check() is None:
            self.path.unlink(missing_ok=True)
        self.owner = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False
