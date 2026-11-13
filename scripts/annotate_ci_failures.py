#!/usr/bin/env python3
"""Turn a test log into GitHub Actions annotations.

CI logs of a public repository need admin rights to download, but the
annotation stream is public, so surfacing failures here makes a red build
diagnosable from the outside (and from `gh api .../check-runs/<id>/annotations`).

    python scripts/annotate_ci_failures.py pytest.log [more.log]
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

MAX_LINES = 8  # keep room for every log in the annotation stream
MAX_CHARS = 900
INTERESTING = ("FAILED", "ERROR", "Error:", "assert", "Exception", "Traceback", "- ")


def interesting_lines(text: str) -> List[str]:
    """The lines worth publishing: failures first, then their context.

    A command that fails without a recognisable message still needs to be
    diagnosable, so the tail of the log is the last resort.
    """
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    picked = [
        line for line in lines if line.startswith(INTERESTING) or line.lstrip().startswith("- ")
    ]
    for line in lines:
        if len(picked) >= MAX_LINES:
            break
        if line not in picked and any(word in line for word in INTERESTING):
            picked.append(line)
    if picked:
        return picked[:MAX_LINES]
    if "passed" in text and "FAILED" not in text and "failed" not in text:
        return []  # the command succeeded; nothing to report
    return lines[-MAX_LINES:]


def annotate(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"::warning::could not read {path}: {exc}")
        return 0
    picked = interesting_lines(text)
    if not picked:
        return 0
    for line in picked:
        message = line.replace("\r", " ")
        print(f"::error::[{path.name}] " + message[:MAX_CHARS])
    return len(picked)


def main(argv: Optional[List[str]] = None) -> int:
    paths = [Path(name) for name in (argv if argv is not None else sys.argv[1:])]
    if not paths:
        print("usage: annotate_ci_failures.py LOG [LOG...]", file=sys.stderr)
        return 2
    total = sum(annotate(path) for path in paths)
    print(f"annotated {total} line(s) from {len(paths)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
