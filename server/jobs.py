"""Job state, progress accounting, cancellation and temp cleanup.

One job at a time. There is deliberately no queue.
"""

from __future__ import annotations

import math
import os
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import calibration, config

STAGE_LABELS = {
    "prepare": "Preparing the pages",
    "load": "Loading the language model",
    "transcribe": "Reading the handwriting",
    "mark": "Marking the composition",
    "correct": "Writing the corrected version",
}

RUN_ORDER = ("prepare", "load", "transcribe", "mark")
CORRECT_ORDER = ("prepare", "load", "correct")


class Cancelled(Exception):
    """Raised inside the pipeline when the user cancels."""


class JobError(Exception):
    """A failure with a message already fit for a non-technical user."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail


@dataclass
class Job:
    id: str
    # "mark" processes photographs; "correct" rewrites the composition that a
    # finished job already produced. Cancelling them means different things --
    # a cancelled mark has nothing behind it, a cancelled correction still has
    # the marked report.
    kind: str = "mark"

    pages: list = field(default_factory=list)     # Paths, in reading order
    level: str = "p5"
    language: str = "en"
    topic: str = ""
    model_key: str = ""
    correction: str = "both"                      # minimal | improved | both

    name: str = ""                                # the output folder's name
    report_path: Optional[Path] = None
    transcript_path: Optional[Path] = None
    corrected_paths: list = field(default_factory=list)
    score: Optional[int] = None

    # Live LlamaServer, so a cancel can drop an in-flight request rather than
    # waiting out a whole call.
    llm_server: object = None

    state: str = "new"          # new | ready | running | done | error | cancelled
    stage: str = "prepare"
    percent: float = 0.0
    message: str = ""
    error: str = ""
    started_at: float = 0.0
    stage_started_at: float = 0.0
    stage_seconds: dict = field(default_factory=dict)
    # Set once the transcription stage knows how many pages are in flight, so
    # the bar can move within the stage rather than jumping per page.
    page_index: int = 0

    cancel_event: threading.Event = field(default_factory=threading.Event)
    procs: list = field(default_factory=list)
    log_lines: list = field(default_factory=list)
    _queues: list = field(default_factory=list)
    _loop: object = None
    _weights: dict = field(default_factory=dict)
    _expected: dict = field(default_factory=dict)

    # -- cancellation ----------------------------------------------------

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled()

    def register_proc(self, proc: subprocess.Popen) -> None:
        self.procs.append(proc)
        assign_to_job_object(proc.pid)

    def unregister_proc(self, proc: subprocess.Popen) -> None:
        try:
            self.procs.remove(proc)
        except ValueError:
            pass

    def kill_processes(self) -> None:
        """Kill each child process tree.

        /T matters: a surviving llama-server keeps VRAM allocated until reboot.
        """
        for proc in list(self.procs):
            if proc.poll() is not None:
                continue
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    timeout=15,
                )
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        self.procs.clear()

    def cancel(self) -> None:
        self.cancel_event.set()
        server = self.llm_server
        if server is not None:
            try:
                server.abort()
            except Exception:  # noqa: BLE001
                pass
        self.kill_processes()

    # -- progress and logging -------------------------------------------

    def emit(self, event: dict) -> None:
        """Push an event to every SSE listener. Safe from worker threads."""
        loop = self._loop
        for q in list(self._queues):
            if loop is not None and loop.is_running():
                try:
                    loop.call_soon_threadsafe(q.put_nowait, event)
                except RuntimeError:
                    pass
            else:
                try:
                    q.put_nowait(event)
                except Exception:  # noqa: BLE001
                    pass

    def log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        line = "[%s] %s" % (stamp, text)
        self.log_lines.append(line)
        _append_job_log(line)
        self.emit({"type": "log", "line": line})

    # -- progress arithmetic ---------------------------------------------

    def order(self) -> tuple:
        return CORRECT_ORDER if self.kind == "correct" else RUN_ORDER

    def _corrections(self) -> int:
        return 2 if self.correction == "both" else 1

    def weights(self) -> dict:
        """Share of the bar per stage, from measured seconds where possible.

        Fixed weights are the wrong shape here. Loading a 16.5 GB model off a
        cold disk takes a minute or two and the whole rest of the job can take
        three, so a machine that has measured itself divides the bar quite
        differently from the defaults -- and on a UMA machine reading a page
        takes longer than everything else put together.
        """
        if self._weights:
            return self._weights
        exp = self._expected_seconds()
        stages = self.order()
        if exp:
            total = sum(exp.get(s, 0.0) for s in stages) or 1.0
            self._weights = {s: 100.0 * exp.get(s, 0.0) / total for s in stages}
        elif self.kind == "correct":
            self._weights = {"prepare": 2.0, "load": 30.0, "correct": 68.0}
        else:
            self._weights = dict(calibration.FALLBACK_SHARES)
        return self._weights

    def _expected_seconds(self) -> dict:
        if self._expected:
            return self._expected
        self._expected = calibration.expected(
            self.model_key, len(self.pages), self.kind, self._corrections())
        return self._expected

    def stage_base(self, stage: str) -> float:
        w = self.weights()
        total = 0.0
        for s in self.order():
            if s == stage:
                break
            total += w.get(s, 0.0)
        return total

    def set_stage(self, stage: str, message: str = "") -> None:
        if stage != self.stage and self.stage_started_at:
            self.stage_seconds[self.stage] = time.time() - self.stage_started_at
        if stage != self.stage:
            self.stage_started_at = time.time()
            self.page_index = 0
        self.stage = stage
        self.message = message or STAGE_LABELS.get(stage, stage)
        self.percent = self.stage_base(stage)
        self.emit(self._status())
        self.log(self.message)

    def set_progress(self, fraction: float, message: str = "") -> None:
        """Progress within the current stage, as a 0..1 fraction of it."""
        fraction = max(0.0, min(1.0, fraction))
        w = self.weights()
        self.percent = self.stage_base(self.stage) + w.get(self.stage, 0.0) * fraction
        if message:
            self.message = message
        self.emit(self._status())

    def tick(self, message: str = "") -> None:
        """Move the bar within a stage from elapsed time alone.

        Used for the stages with nothing to count -- loading the weights, and
        the inside of a single model call. The curve is 1 - e^(-t/expected),
        which is the point: however wrong the expectation is, the bar cannot
        stall at a number and cannot overshoot the stage it is in.

        Never `tokens / max_tokens`. max_tokens is a cap, not an expectation --
        the marking cap is 3000 and a real report is 800-1200 -- so that
        formula crawls to a third and then jumps.
        """
        expected_s = self._expected_seconds().get(self.stage, 0.0)
        if expected_s <= 0:
            expected_s = {"load": 90.0, "transcribe": 60.0,
                          "mark": 120.0, "correct": 120.0}.get(self.stage, 60.0)
        elapsed = time.time() - (self.stage_started_at or time.time())
        fraction = 1.0 - math.exp(-elapsed / max(expected_s, 1.0))
        # Pages give a real count to work from; blend it with the curve so the
        # bar neither stalls between pages nor jumps backwards on a fast one.
        if self.stage == "transcribe" and self.pages:
            counted = self.page_index / float(len(self.pages))
            fraction = max(fraction * 0.5, counted)
        self.set_progress(fraction, message)

    def _status(self) -> dict:
        return {
            "type": "status",
            "state": self.state,
            "stage": self.stage,
            "stage_label": STAGE_LABELS.get(self.stage, self.stage),
            "percent": round(self.percent, 2),
            "message": self.message,
            "elapsed": round(time.time() - self.started_at, 1) if self.started_at else 0,
            "remaining": self.remaining_seconds(),
        }

    def remaining_seconds(self):
        """Time left, counted from the work still to do -- not from the bar.

        Extrapolating elapsed / percent looks reasonable and is a lie: the
        bar's position inside a model call is a deliberately asymptotic curve,
        not a measurement, so dividing by it reports seconds remaining with
        minutes still to run.

        What remains here is a known list of stages, each priced from this
        machine's own measurements. Before those exist, the honest answer is
        None, and the UI says so in words.
        """
        if not self.started_at or self.state != "running":
            return None
        exp = self._expected_seconds()
        if not exp:
            return None
        stages = self.order()
        try:
            current = stages.index(self.stage)
        except ValueError:
            return None

        # What is left of the stage in flight, from the SAME curve the bar
        # uses. Subtracting elapsed from the expectation instead was the
        # obvious thing and it was wrong twice over: it hits zero the moment a
        # stage runs longer than its median -- which is half of all runs -- and
        # it disagreed with a bar that was still climbing. A screenshot caught
        # both, reading "73%" and "about 0s left" at the same moment.
        #
        # remaining = T * e^(-t/T) is what `tick`'s 1 - e^(-t/T) implies. It
        # falls off with elapsed time, never reaches zero while the stage is
        # running, and by construction the two displays now tell one story.
        expected_s = exp.get(self.stage, 0.0)
        if self.stage == "transcribe" and self.pages:
            done = self.page_index / float(len(self.pages))
            in_flight = expected_s * max(0.0, 1.0 - done)
        elif expected_s > 0:
            elapsed = time.time() - (self.stage_started_at or time.time())
            in_flight = expected_s * math.exp(-elapsed / expected_s)
        else:
            in_flight = 0.0
        total = max(in_flight, 0.0)
        for s in stages[current + 1:]:
            total += exp.get(s, 0.0)
        return max(0, int(round(total)))

    def push_status(self) -> None:
        self.emit(self._status())

    # -- terminal states -------------------------------------------------

    def finish(self) -> None:
        if self.stage_started_at:
            self.stage_seconds[self.stage] = time.time() - self.stage_started_at
        self.state = "done"
        self.percent = 100.0
        self.message = "Finished"
        self.emit({
            "type": "done",
            "elapsed": round(time.time() - self.started_at, 1),
            "score": self.score,
        })

    def fail(self, message: str, detail: str = "") -> None:
        self.state = "error"
        self.error = message
        self.message = message
        if detail:
            _append_job_log(detail)
        self.emit({"type": "error", "message": message})

    def mark_cancelled(self) -> None:
        self.state = "cancelled"
        self.message = "Cancelled"
        self.emit({"type": "cancelled"})


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def create(job_id: str) -> Job:
    with _lock:
        job = Job(id=job_id)
        _jobs[job_id] = job
        return job


def get(job_id: str) -> Optional[Job]:
    return _jobs.get(job_id)


def active() -> Optional[Job]:
    for job in _jobs.values():
        if job.state == "running":
            return job
    return None


# ---------------------------------------------------------------------------
# job log
# ---------------------------------------------------------------------------

_log_lock = threading.Lock()


def job_log_path() -> Path:
    return config.TEMP / "job.log"


def _append_job_log(text: str) -> None:
    with _log_lock:
        try:
            config.TEMP.mkdir(parents=True, exist_ok=True)
            with open(job_log_path(), "a", encoding="utf-8", errors="replace") as fh:
                fh.write(text.rstrip() + "\n")
        except OSError:
            pass


def log_exception(exc: BaseException) -> str:
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    _append_job_log(detail)
    return detail


def read_job_log() -> str:
    """The bundle behind the "Copy diagnostic info" button.

    Includes the tail of llama-server's own log: when a model stage fails the
    reason is almost always in there and almost never in ours.
    """
    parts = []
    try:
        parts.append(job_log_path().read_text(encoding="utf-8", errors="replace"))
    except OSError:
        pass
    llama = config.TEMP / "llama-server.log"
    try:
        tail = llama.read_text(encoding="utf-8", errors="replace").splitlines()[-60:]
        if tail:
            parts.append("\n--- llama-server (last %d lines) ---\n" % len(tail)
                         + "\n".join(tail))
    except OSError:
        pass
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# temp cleanup
# ---------------------------------------------------------------------------

_TEMP_GLOBS = ["pages", "llama-server.log", "*.tmp", "*.partial"]


def clean_temp(keep_log: bool = True) -> None:
    """Empty temp\\. The uploaded pages live there and nothing else needs them.

    A dozen phone photographs is 30-40 MB per job. That is not the gigabyte an
    audio recording was, but a user who marks twenty compositions should still
    not be quietly accumulating them.
    """
    import shutil

    if not config.TEMP.exists():
        return
    for pattern in _TEMP_GLOBS:
        for path in config.TEMP.glob(pattern):
            try:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink()
            except OSError:
                pass
    if not keep_log:
        try:
            job_log_path().unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# instance lock
# ---------------------------------------------------------------------------

# run.bat scans ports 8000-8005 and starts on whichever is free, so a second
# copy of the app can be launched while the first is mid-job -- and the startup
# sweep would then delete the pages the first one is still reading, which
# surfaces as a marking failure with no visible cause. Observed in development,
# exactly that way.
#
# The lock records this process's PID. A startup that finds a live PID in it
# leaves temp\ alone; a stale one -- from a run that was force-quit -- is
# ignored and overwritten, which is the case the sweep exists for.

def lock_path() -> Path:
    return config.TEMP / "running.lock"


def another_instance_running() -> bool:
    try:
        pid = int(lock_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    if pid == os.getpid():
        return False
    return _pid_alive(pid)


def _pid_alive(pid: int) -> bool:
    """Is that PID a live python.exe? Checked by name as well as by number.

    A bare "does this PID exist" would be wrong: Windows reuses PIDs, and
    treating a recycled number as a live instance would disable the sweep
    permanently.
    """
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "PID eq %d" % pid, "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return False        # cannot tell; sweeping is the safer default
    return "python" in out.lower()


def mark_running() -> None:
    try:
        config.TEMP.mkdir(parents=True, exist_ok=True)
        lock_path().write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass


def clear_running() -> None:
    try:
        if lock_path().read_text(encoding="utf-8").strip() == str(os.getpid()):
            lock_path().unlink()
    except (OSError, ValueError):
        pass


def shutdown_all() -> None:
    """Kill every child process and clear temp. Safe to call more than once."""
    for job in list(_jobs.values()):
        try:
            job.cancel_event.set()
            job.kill_processes()
        except Exception:  # noqa: BLE001
            pass
    clear_running()
    clean_temp()


# ---------------------------------------------------------------------------
# kill-on-close job object
# ---------------------------------------------------------------------------

# atexit and signal handlers do not run when a console window is force-quit,
# which would strand llama-server.exe holding VRAM until reboot. A Windows job
# object with KILL_ON_JOB_CLOSE is the only mechanism the OS honours
# unconditionally: when this process dies for any reason, its handle closes and
# every process assigned to the job is terminated with it.

_job_handle = None

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


def install_kill_on_close() -> None:
    global _job_handle
    if _job_handle is not None or os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            kernel32.CloseHandle(handle)
            return
        _job_handle = handle
    except Exception:  # noqa: BLE001 - best effort; taskkill remains the fallback
        _job_handle = None


def assign_to_job_object(pid: int) -> None:
    """Put a spawned process into the kill-on-close job object."""
    if _job_handle is None or os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        PROCESS_SET_QUOTA, PROCESS_TERMINATE = 0x0100, 0x0001
        h = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        if not h:
            return
        try:
            kernel32.AssignProcessToJobObject(_job_handle, h)
        finally:
            kernel32.CloseHandle(h)
    except Exception:  # noqa: BLE001
        pass
