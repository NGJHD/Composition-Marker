r"""Build the two release assets. Development only; not shipped.

    runtime\python.exe tools\make_release.py

A release carries two zips and they are not variants of the same thing:

  Composition-Marker-vX.Y.Z.zip        the source tree, ~150 KB
  Composition-Marker-vX.Y.Z-full.zip   the same plus runtime\, bin\ and mmproj

The small one is the **update payload** -- what the in-app updater downloads and
robocopies over an install. It must stay small and must never contain
`runtime\python.exe`: the updater would be overwriting the interpreter the
running app is executing from, and a half-copied interpreter cannot start, so it
cannot self-repair. `version.pick_asset` skips anything with `-full` in the name
for exactly that reason.

The full one is for a **first install**: unzip it and only the two language
models are left to download. Everything in it is the set that was actually
tested, rather than whatever the pinned URLs happen to serve later.

Carried over from the Meeting Summariser's tools\make_release.py, which is where
the shape of all this was worked out. Two deliberate differences are noted where
they occur: what travels in the full bundle, and config.json.
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from server import version  # noqa: E402

PREFIX = "Composition-Marker"
BUNDLED_DIRS = ("runtime", "bin")

# The vision projector travels; the two language models do not. mmproj is
# 886 MB and it is not optional -- without it llama-server starts happily,
# accepts an image, and answers about a page it never saw (CLAUDE.md section 4),
# so an install missing it fails in the worst possible way rather than refusing.
#
# The language models are 13.5 GB and 7.3 GB. Each is individually past GitHub's
# 2 GB per-asset limit, so DOWNLOAD_MODELS.bat remains the only way to get them.
BUNDLED_FILES = ("models/mmproj-F16.gguf",)

SKIP_PARTS = {"__pycache__"}

# Tracked so a release is reproducible from the repository, but development
# tooling has no business in an install.
EXCLUDE_PREFIXES = ("tools/",)

# config.json is the operator's file: CLAUDE.md section 4 says they edit it in
# Notepad, and config.py merges whatever is there over its own _DEFAULTS, so the
# file is optional. Shipping it in the **update payload** would have robocopy
# overwrite their tunables -- an edited file differs in size and timestamp, so
# robocopy's skip does not save it. Leaving it out means their settings survive
# an update and any newly added tunable simply picks up its default in code.
#
# It does travel in the full bundle, because a first install has nothing to
# overwrite and a file to open in Notepad is friendlier than an absent one.
# This is a deliberate difference from the Meeting Summariser, which ships it in
# both.
PAYLOAD_EXCLUDE = {"config.json"}


def source_entries() -> list:
    """Every tracked file, so the zip matches the commit rather than the disk."""
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                         text=True, check=True)
    return [line for line in out.stdout.splitlines()
            if line.strip() and not line.startswith(EXCLUDE_PREFIXES)]


def write(target: Path, extras: bool) -> None:
    files = source_entries()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for rel in files:
            if not extras and rel in PAYLOAD_EXCLUDE:
                continue
            src = ROOT / rel
            if src.is_file():
                z.write(src, "%s/%s" % (PREFIX, rel))
        if not extras:
            return
        for folder in BUNDLED_DIRS:
            base = ROOT / folder
            if not base.is_dir():
                raise SystemExit(
                    "missing %s\\ -- the full bundle needs it" % folder)
            for src in base.rglob("*"):
                if not src.is_file() or SKIP_PARTS & set(src.parts):
                    continue
                z.write(src, "%s/%s" % (PREFIX, src.relative_to(ROOT).as_posix()))
        for rel in BUNDLED_FILES:
            src = ROOT / rel
            if not src.is_file():
                raise SystemExit(
                    "missing %s -- run DOWNLOAD_MODELS.bat first" % rel)
            z.write(src, "%s/%s" % (PREFIX, rel))


def main() -> None:
    v = version.APP_VERSION
    # One level up from the repository on purpose, so a 1.6 GB file never sits
    # in the working tree waiting to be committed by accident.
    out = ROOT.parent
    plain = out / ("%s-v%s.zip" % (PREFIX, v))
    full = out / ("%s-v%s-full.zip" % (PREFIX, v))

    for target, extras in ((plain, False), (full, True)):
        print("building %s ..." % target.name, flush=True)
        if target.exists():
            target.unlink()
        write(target, extras)
        size = target.stat().st_size
        print("  %d bytes (%.2f GB)" % (size, size / 1e9), flush=True)
        if size > 2_000_000_000:
            raise SystemExit(
                "%s exceeds GitHub's 2 GB per-asset limit" % target.name)

    print("\nassets for v%s:" % v)
    print("  %s   <- update payload, what pick_asset chooses" % plain.name)
    print("  %s   <- first install" % full.name)


if __name__ == "__main__":
    main()
