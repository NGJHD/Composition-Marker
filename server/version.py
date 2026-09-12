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
APP_VERSION = "1.1.0"

GITHUB_REPO = "NGJHD/Composition-Marker"
REPO_URL = "https://github.com/%s" % GITHUB_REPO

# Which asset on a release is the one to install. The release carries the
# source only -- bin\, models\ and runtime\ are 23 GB of payload that an
# update never needs to touch, because robocopy leaves what it does not carry
# alone. The tag must match APP_VERSION exactly or the updater refuses the
# download rather than installing a version that disagrees with its own label.
ASSET_SUFFIX = "-source.zip"


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
