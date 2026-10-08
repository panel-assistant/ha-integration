"""Check that a published installer copy is byte-identical to a local build.

Usage: verify_installer_copy.py <built-directory> <published-base-url>

Every file in the built directory is fetched from the same relative path under
the base URL and compared by SHA-256. Pages reports a deployment finished before
its CDN serves it, so a mismatch is retried for a while before it fails.
"""

from __future__ import annotations

import hashlib
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

ATTEMPTS = 20
DELAY_SECONDS = 30.0
TIMEOUT_SECONDS = 30.0


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def differences(built: Path, base_url: str) -> list[str]:
    """Return one line per file that is missing or different at the base URL."""
    base = base_url if base_url.endswith("/") else base_url + "/"
    files = sorted(path for path in built.rglob("*") if path.is_file())
    if not files:
        return [f"{built}: nothing was built"]
    problems = []
    for path in files:
        relative = path.relative_to(built).as_posix()
        url = base + quote(relative)
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
                published = response.read()
        except (urllib.error.URLError, OSError) as err:
            problems.append(f"{relative}: not served ({err})")
            continue
        if _digest(published) != _digest(path.read_bytes()):
            problems.append(f"{relative}: published bytes differ from the build")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    built, base_url = Path(argv[0]), argv[1]
    for attempt in range(1, ATTEMPTS + 1):
        problems = differences(built, base_url)
        if not problems:
            print(f"{base_url}: every built file is published unchanged")
            return 0
        if attempt < ATTEMPTS:
            time.sleep(DELAY_SECONDS)
    for problem in problems:
        print(f"{base_url} {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
