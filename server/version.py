"""App identity and version arithmetic.

The only file that changes when this code is reused for a different app, and
the only place the version number lives. Everything here is pure -- no network,
no filesystem -- so the comparison logic can be exercised without downloading
anything.
"""

from __future__ import annotations

import re

APP_NAME = "Composition Marker"
APP_AUTHOR = "Darren Ng"
APP_VERSION = "1.2.0"

GITHUB_REPO = "NGJHD/Composition-Marker"
REPO_URL = "https://github.com/%s" % GITHUB_REPO

# A release carries two zips and they are not variants of the same thing:
#
#   Composition-Marker-vX.Y.Z.zip        the source tree, ~150 KB
#   Composition-Marker-vX.Y.Z-full.zip   the same plus runtime\, bin\ and mmproj
#
# The small one is the update payload. It must never contain runtime\python.exe:
# the updater would be overwriting the interpreter the running app is executing
# from, and a half-copied interpreter cannot start, so it cannot self-repair.
# pick_asset skips anything carrying this marker for exactly that reason.
#
# The full one is for a first install: unzip it and only the two language models
# are left to download.
FULL_ASSET_MARKER = "-full"


def parse_version(text: str):
    """"v1.2.0" -> (1, 2, 0). None if it cannot be read as one."""
    if not text:
        return None
    m = re.match(r"^\s*v?(\d+)\.(\d+)(?:\.(\d+))?", str(text))
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def is_newer(candidate: str, current: str = APP_VERSION) -> bool:
    """Numeric comparison. "1.10.0" > "1.9.0" is false as strings."""
    a, b = parse_version(candidate), parse_version(current)
    if a is None or b is None:
        return False
    return a > b


def pick_asset(assets: list):
    """The source zip attached to a release -- the update payload.

    Anything carrying FULL_ASSET_MARKER is a first-install bundle and is
    skipped. Any other ambiguity is refused rather than guessed at: offering
    the wrong asset would overwrite an install with something unintended.

    A release carrying only a full bundle yields nothing, which is the correct
    answer -- "nothing to install" rather than "install the 1.6 GB one over the
    interpreter you are running".
    """
    zips = [a for a in (assets or [])
            if str(a.get("name", "")).lower().endswith(".zip")
            and a.get("browser_download_url")]
    updates = [a for a in zips
               if FULL_ASSET_MARKER not in str(a.get("name", "")).lower()]
    return updates[0] if len(updates) == 1 else None
