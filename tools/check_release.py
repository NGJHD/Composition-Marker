r"""Check a published release the way a user's copy would see it.

    runtime\python.exe tools\check_release.py

Run this after `gh release create`. It asks GitHub the same question the in-app
update button asks and reports what an installed copy would actually do, which
is the part you cannot verify by looking at the releases page.

The check that matters most is the tag-against-APP_VERSION one. A release whose
code still says the old version downloads fine and is then refused by the
updater's own verification -- deliberate, since that check is what stops a
tampered or mismatched zip being installed, but it means a forgotten version
bump produces a release nobody can install.

Development only; excluded from both release zips.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import updater, version  # noqa: E402

failures: list = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    print("  %-4s %s%s" % ("ok" if ok else "FAIL", label,
                           ("  --  " + detail) if detail else ""))
    if not ok:
        failures.append(label)
    return ok


def main() -> int:
    installed = version.APP_VERSION
    print("\nInstalled version in server/version.py: %s\n" % installed)

    print("What GitHub reports as the latest release")
    try:
        data = updater._get_json(updater.API)
    except Exception as exc:  # noqa: BLE001
        print("  FAIL could not reach GitHub  --  %s" % exc)
        return 1

    tag = str(data.get("tag_name") or "")
    assets = data.get("assets") or []
    names = [str(a.get("name", "")) for a in assets]

    check(bool(tag), "the release has a tag", tag)
    check(version.parse_version(tag) is not None,
          "the tag parses as a version", tag)
    check(tag == "v" + installed,
          "the tag matches APP_VERSION exactly",
          "tag %s against version %s" % (tag, installed))
    check(not data.get("prerelease"),
          "it is not a pre-release",
          "a pre-release is invisible to /releases/latest")
    check(not data.get("draft"), "it is published, not a draft")

    print("\nThe assets")
    for n in names:
        print("       %s" % n)
    if not names:
        print("       (none)")

    payload = version.pick_asset(assets)
    check(payload is not None,
          "exactly one non-full zip, so pick_asset can choose",
          payload["name"] if payload else "pick_asset returned nothing")
    check(any(version.FULL_ASSET_MARKER in n.lower() for n in names),
          "a -full bundle is attached for first installs")

    if payload:
        check(updater.is_our_asset_url(payload.get("browser_download_url", "")),
              "the download URL passes the repository check")
        size = int(payload.get("size") or 0)
        check(0 < size < 5_000_000,
              "the payload is small, so it carries no runtime or binaries",
              "%.0f KB" % (size / 1024))

    print("\nWhat an installed copy would do")
    for older in ("0.9.0", installed):
        newer = version.is_newer(tag, older)
        print("  a copy on %-7s -> %s" % (
            older, "offered %s" % tag if newer else "told it is up to date"))

    print()
    if failures:
        print("%d CHECK(S) FAILED: %s" % (len(failures), ", ".join(failures)))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
