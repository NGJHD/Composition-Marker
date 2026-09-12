"""Check GitHub for a newer release, download it, and replace this folder.

**This is the one part of the application that touches the network, and it only
does so when the user presses a button.** CLAUDE.md section 0 was written
saying there would be no update button and therefore no outbound request ever;
that was reversed at the operator's instruction and the constraint now reads
"no outbound request except the update check, and only on a button press". No
other code path in the app opens a socket to anything but 127.0.0.1, and a
machine that never presses the button never resolves a hostname.

The workflow is the one in the operator's UPDATE_BUTTON.md, adapted from an
Electron app to a folder of Python. Nine steps, and every failure leaves the
old installation running and untouched:

    check -> pick the .zip -> is the folder writable? -> download to %TEMP%
          -> unpack with bsdtar -> verify it is this app and the version
             matches the tag -> write a .cmd -> launch it -> quit

The application cannot replace itself while it is running, because Windows
holds runtime\\python.exe open. That is the only reason the .cmd exists.

**What the release zip contains is the source, not the payload.** bin\\,
models\\ and runtime\\ are gitignored and total about 23 GB; an update is a few
hundred kilobytes of Python, prompts and frontend. robocopy leaves everything
it does not carry alone, so the weights are never re-downloaded.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config, version

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

API = "https://api.github.com/repos/%s/releases/latest" % version.GITHUB_REPO

# Anonymous GitHub API calls are limited to 60 an hour per address. A button a
# person presses will never approach that, and a token must not be shipped in
# an application anyway.
HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "%s/%s" % (version.APP_NAME.replace(" ", "-"),
                             version.APP_VERSION),
}

# Everything this module creates in %TEMP% carries this prefix, and nothing
# without it is ever deleted. Pointed at the wrong folder, the rd /s /q at the
# end of the script would take a real one with it.
STAGING_PREFIX = "CompositionMarker-update-"

# A machine that loses power mid-update leaves a whole unpacked copy in %TEMP%,
# and the .cmd cannot delete itself. Both get swept on the next check.
SWEEP_AFTER_S = 24 * 60 * 60

# What proves the unpacked zip is this application and not something else.
MARKERS = ("server/main.py", "web/index.html", "run.bat", "server/version.py")


class UpdateError(Exception):
    """Something the user should be told in a sentence."""


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------

class _State:
    """One update at a time, and the UI polls this.

    Deliberately not a Job: an update is not a marking run, it must be
    available while a job is running, and it survives into the seconds after
    the server has been asked to stop.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.phase = "idle"      # idle|downloading|unpacking|verifying|ready|failed
        self.received = 0
        self.total = 0
        self.message = ""
        self.cancel = threading.Event()
        self.staging: Path | None = None

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "phase": self.phase,
                "received": self.received,
                "total": self.total,
                "message": self.message,
                # Cancel is a lie once there is nothing left to abort.
                "cancellable": self.phase == "downloading",
            }

    def set(self, phase: str, message: str = "") -> None:
        with self.lock:
            self.phase = phase
            if message:
                self.message = message


STATE = _State()


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

def _get_json(url: str, timeout: float = 15.0) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError(
                "There are no published releases to compare against yet."
            ) from exc
        if exc.code in (403, 429):
            raise UpdateError(
                "GitHub is asking us to wait before checking again. Try in "
                "an hour."
            ) from exc
        raise UpdateError(
            "GitHub answered with an error (%s). Try again later." % exc.code
        ) from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        raise UpdateError(
            "Could not reach GitHub. Check the internet connection — this is "
            "the only thing in this application that needs one."
        ) from exc
    except ValueError as exc:
        raise UpdateError("GitHub's answer could not be read.") from exc


def pick_asset(release: dict):
    """The update payload on this release, or None.

    Delegates the choosing to version.pick_asset, which is pure and therefore
    testable without a network: it skips the -full first-install bundle, which
    must never be robocopied over a running install because it carries the
    interpreter the app is executing from.

    GitHub's own "Source code (zip)" is not an asset -- it is `zipball_url` --
    so a release with nothing attached returns None, and the user is told the
    release cannot be installed automatically rather than being handed a
    download that is missing run.bat.
    """
    return version.pick_asset(release.get("assets") or [])


def is_our_asset_url(url: str) -> bool:
    """Only a release asset from this application's own repository.

    The URL reaches the install endpoint from the page, which means it is not
    to be trusted to point wherever it likes -- the same reason UPDATE_BUTTON.md
    re-checks the prefix in the main process rather than believing the
    renderer. A general-purpose "download and run this" bridge is a hole worth
    not opening, and this one ends in code being copied over the application.
    """
    prefix = "https://github.com/%s/releases/download/" % version.GITHUB_REPO
    return url.startswith(prefix) and ".." not in url


def check() -> dict:
    """What the Check button reports. Never raises for "no update"."""
    sweep()
    release = _get_json(API)
    tag = str(release.get("tag_name") or "")
    parsed = version.parse_version(tag)
    asset = pick_asset(release)

    out = {
        "current": version.APP_VERSION,
        "latest": tag,
        "newer": version.is_newer(tag),
        "notes": (release.get("body") or "")[:4000],
        "url": release.get("html_url") or version.REPO_URL,
        "asset": None,
        "installable": False,
        "why": "",
    }
    if parsed is None:
        # An unparseable tag answers "not newer". Never offer an update that
        # cannot be reasoned about.
        out["newer"] = False
        out["why"] = "The latest release is not numbered in a way this can compare."
        return out
    if not out["newer"]:
        return out
    if asset is None:
        out["why"] = ("Release %s has no downloadable zip attached, so it "
                      "cannot be installed automatically. The link below has "
                      "it." % tag)
        return out

    writable, reason = folder_writable()
    if not writable:
        out["why"] = reason
        out["asset"] = {"name": asset["name"], "size": int(asset.get("size") or 0)}
        return out

    out["asset"] = {"name": asset["name"], "size": int(asset.get("size") or 0),
                    "url": asset["browser_download_url"]}
    out["installable"] = True
    return out


def folder_writable() -> tuple[bool, str]:
    """Checked before the download, not after.

    A folder under Program Files cannot replace itself, and finding that out
    after the download is rude.
    """
    root = config.ROOT
    probe = root / (".update-write-test-%d" % os.getpid())
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        return (False,
                "This folder cannot be written to, so it cannot update "
                "itself. Move the application somewhere like your Desktop, "
                "or download the new version by hand.")
    return (True, "")


# ---------------------------------------------------------------------------
# staging
# ---------------------------------------------------------------------------

def sweep() -> None:
    """Delete our own abandoned staging folders and scripts. Only ours."""
    tmp = Path(tempfile.gettempdir())
    now = time.time()
    try:
        entries = list(tmp.iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.name.startswith(STAGING_PREFIX):
            continue
        try:
            if now - entry.stat().st_mtime < SWEEP_AFTER_S:
                continue
        except OSError:
            continue
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            try:
                entry.unlink()
            except OSError:
                pass


def _download(url: str, target: Path, expected: int) -> None:
    STATE.set("downloading")
    with STATE.lock:
        STATE.received, STATE.total = 0, expected
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length") or expected or 0)
            with STATE.lock:
                STATE.total = total
            with open(target, "wb") as fh:
                while True:
                    if STATE.cancel.is_set():
                        raise UpdateError("The download was cancelled.")
                    chunk = resp.read(262144)
                    if not chunk:
                        break
                    fh.write(chunk)
                    with STATE.lock:
                        STATE.received += len(chunk)
    except UpdateError:
        raise
    except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout,
            TimeoutError, OSError) as exc:
        raise UpdateError(
            "The download did not finish. Nothing has been changed."
        ) from exc

    size = target.stat().st_size if target.exists() else 0
    if size == 0 or (expected and size < expected * 0.9):
        raise UpdateError("The download came back incomplete. Nothing has "
                          "been changed.")


def _unpack(zip_path: Path, dest: Path) -> None:
    """bsdtar, which Windows has shipped in System32 since 10 1803.

    The absolute path matters: tar.exe on PATH may be the GNU one that Git for
    Windows installs, and that cannot read a zip at all.
    """
    STATE.set("unpacking")
    dest.mkdir(parents=True, exist_ok=True)
    bsdtar = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    if bsdtar.exists():
        proc = subprocess.run(
            [str(bsdtar), "-xf", str(zip_path), "-C", str(dest)],
            capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
        if proc.returncode == 0:
            return
        detail = (proc.stderr or "").strip()[:400]
    else:
        detail = "tar.exe is not in System32"

    # Anything older, or a bsdtar that refused: PowerShell can do it slowly.
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         "Expand-Archive -LiteralPath '%s' -DestinationPath '%s' -Force"
         % (zip_path, dest)],
        capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    if proc.returncode != 0:
        raise UpdateError(
            "The downloaded file could not be unpacked. Nothing has been "
            "changed. (%s)" % (detail or (proc.stderr or "").strip()[:200]))


def _find_root(unpacked: Path) -> Path:
    """The folder holding the app, which a zip usually nests one level down."""
    def looks_right(base: Path) -> bool:
        return all((base / m).exists() for m in MARKERS)

    if looks_right(unpacked):
        return unpacked
    try:
        children = [c for c in unpacked.iterdir() if c.is_dir()]
    except OSError:
        children = []
    for child in children:
        if looks_right(child):
            return child
    raise UpdateError(
        "The downloaded release does not look like this application, so it "
        "has not been installed. Nothing has been changed.")


def _version_in(root: Path) -> str:
    """APP_VERSION out of the downloaded version.py, without importing it.

    Importing would execute a file just downloaded off the internet before it
    has been verified, which is the wrong order to do those two things in.
    """
    try:
        text = (root / "server" / "version.py").read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', text, re.M)
    return m.group(1) if m else ""


def _verify(root: Path, tag: str) -> None:
    """Read it and it disagrees is a hard stop; cannot read it is not.

    A mismatch means a failed update rather than a silent downgrade loop.
    """
    STATE.set("verifying")
    found = _version_in(root)
    if not found:
        return
    want, got = version.parse_version(tag), version.parse_version(found)
    if want and got and want != got:
        raise UpdateError(
            "The download says it is version %s but the release is tagged %s, "
            "so it has not been installed. Nothing has been changed."
            % (found, tag))


# ---------------------------------------------------------------------------
# the script that does what the running app cannot
# ---------------------------------------------------------------------------

def _script(ready: Path, target: Path, staging: Path, log: Path) -> str:
    """A .cmd that waits for the lock, copies, restarts, and deletes itself.

    Paths are baked in rather than passed as arguments: the folder can sit on
    a mapped drive with spaces in it, and a `set "TARGET=..."` line has no
    quoting left to get wrong.

    The wait is on the *file lock*, not on tasklist. `tasklist | find` hangs
    forever when launched detached from a dying parent -- find.exe sits on its
    end of the pipe, the copy never runs, and a console window is left on the
    desktop. Opening the exe for append and running a no-op writes zero bytes
    and simply fails while the file is held.
    """
    exe = config.ROOT / "runtime" / "python.exe"
    return "\r\n".join([
        "@echo off",
        # detached implies DETACHED_PROCESS, which beats windowsHide, so this
        # gets its own console for a second or two. Name it so it reads as
        # intentional rather than as something that escaped.
        "title Updating %s" % version.APP_NAME,
        'set "EXE=%s"' % exe,
        'set "READY=%s"' % ready,
        'set "TARGET=%s"' % target,
        'set "STAGE=%s"' % staging,
        'set "LOG=%s"' % log,
        "set TRIES=0",
        'echo waiting for the application to close >>"%LOG%"',
        ":waitloop",
        '2>nul (>>"%EXE%" call ) && goto exited',
        "set /a TRIES+=1",
        "if %TRIES% GEQ 60 goto copy",
        "ping -n 2 127.0.0.1 >nul",
        "goto waitloop",
        ":exited",
        'echo closed after %TRIES% tries >>"%LOG%"',
        ":copy",
        # /E keeps the tree, /R:3 /W:2 retries a file antivirus is still
        # holding, and robocopy skips anything whose size and timestamp match
        # already -- which is why a code-only update lands in a second.
        # No /MIR: mirroring would delete whatever the user keeps in the folder
        # that the release does not carry, output\\ included.
        'robocopy "%READY%" "%TARGET%" /E /R:3 /W:2 /NFL /NDL /NJH /NJS /NP >>"%LOG%" 2>&1',
        # 0-7 are success. `if errorlevel 1` would call every good copy a
        # failure.
        "if errorlevel 8 (",
        '  echo robocopy failed >>"%LOG%"',
        "  echo.",
        "  echo   The update could not be applied. The application has not",
        "  echo   been changed. Details: %LOG%",
        "  echo.",
        "  pause",
        '  rd /s /q "%STAGE%" 2>nul',
        '  (goto) 2>nul & del "%~f0"',
        "  exit /b 1",
        ")",
        'echo copied >>"%LOG%"',
        'cd /d "%TARGET%"',
        'start "" "%TARGET%\\run.bat"',
        # The script cannot delete itself while it runs, so it jumps out of
        # its own context first. Anything missed is swept from %TEMP% on the
        # next check.
        'rd /s /q "%STAGE%" 2>nul',
        '(goto) 2>nul & del "%~f0"',
        "",
    ])


def install(asset: dict, tag: str, on_ready) -> None:
    """Download, unpack, verify, then hand over to the script and quit.

    `on_ready` is called once the new copy is staged and the script is
    running; it is what asks uvicorn to stop, which releases the lock the
    script is waiting on.
    """
    STATE.cancel.clear()
    staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX))
    with STATE.lock:
        STATE.staging = staging
    try:
        zip_path = staging / "release.zip"
        _download(asset["url"], zip_path, int(asset.get("size") or 0))
        unpacked = staging / "unpacked"
        _unpack(zip_path, unpacked)
        ready = _find_root(unpacked)
        _verify(ready, tag)

        log = staging.parent / (STAGING_PREFIX + "log-%d.txt" % os.getpid())
        script = staging.parent / (STAGING_PREFIX + "apply-%d.cmd" % os.getpid())
        script.write_text(_script(ready, config.ROOT, staging, log),
                          encoding="ascii", errors="replace")

        # cmd.exe is a real executable and the script stays its own argument.
        # Never shell=True with an interpolated path -- that is the thing the
        # CVE-2024-27980 fix exists for, and the same rule applies here.
        subprocess.Popen(
            [os.environ.get("ComSpec") or r"C:\Windows\System32\cmd.exe",
             "/c", str(script)],
            cwd=str(staging.parent),
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
        STATE.set("ready", "Restarting to finish the update.")
    except UpdateError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        STATE.set("failed", str(exc))
        raise
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(staging, ignore_errors=True)
        STATE.set("failed", "The update could not be applied. Nothing has "
                            "been changed.")
        raise UpdateError(str(exc)) from exc

    # Only after the script is safely running.
    on_ready()

