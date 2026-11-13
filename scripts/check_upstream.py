#!/usr/bin/env python3
"""Are the Neovim releases pinned here still the ones Neovim publishes?

    python scripts/check_upstream.py             # report; drift alone is not fatal
    python scripts/check_upstream.py --strict    # non-zero unless every pin is current
    python scripts/check_upstream.py --no-checksums

Two questions, both answered from GitHub:

* **is the pin current?**  Each release line must be pinned to the newest patch
  of that line, and a release line newer than the newest one supported here is
  worth knowing about (``0.13`` shipping is not a bug, but it should be a
  decision).
* **is the pin right?**  Every pinned ``(sha256, size)`` must match the
  checksum Neovim published for that archive.  A typo in ``platforms.py``
  otherwise surfaces only when a user downloads the build, long after the
  wheel that claimed it was validated.

Checksums come from the ``digest`` field of the release API; for archives
uploaded before GitHub recorded digests (all of 0.10.x), the release's own
``.sha256sum`` file is fetched instead -- about 90 bytes, so nothing large is
downloaded.  The release list is one API call (two if it ever exceeds 100
entries); set ``GITHUB_TOKEN`` to lift the anonymous 60 requests/hour limit.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fast_nvim_release import bundle  # noqa: E402
from fast_nvim_release.platforms import (  # noqa: E402
    PLATFORMS,
    RELEASE_KEYS,
    RELEASES,
    Release,
    Target,
)

API = "https://api.github.com/repos/neovim/neovim/releases"
MAX_PAGES = 10
PAGE_SIZE = 100
#: A dropped connection is worth one more try; a 403 (rate limit) is not.
ATTEMPTS = 2
RETRY_DELAY = 0.5

#: ``v0.12.6`` -> line ``0.12``, patch 6.  Neovim's other tags ("nightly",
#: "stable") and pre-1.0 lines (``v0.2.2``) do not match on purpose.
TAG = re.compile(r"^v(?P<line>\d+\.\d+)\.(?P<patch>\d+)$")
SHA256 = re.compile(r"(?P<sha>[0-9a-f]{64})", re.IGNORECASE)
LINK_NEXT = re.compile(r'<(?P<url>[^>]+)>\s*;\s*rel="(?P<rel>[^"]+)"')

#: A release line's state against upstream.
OK = "OK"
OUTDATED = "OUTDATED"  #: upstream has a newer patch of this line
AHEAD = "AHEAD"  #: the pin is newer than anything upstream publishes (a typo)
MISSING = "MISSING"  #: upstream publishes no releases for this line at all


class UpstreamError(RuntimeError):
    """GitHub could not be asked, or answered with something unusable."""


# --------------------------------------------------------------------------
# asking GitHub
# --------------------------------------------------------------------------
def _request(url: str, token: Optional[str], timeout: float) -> Tuple[bytes, object]:
    """One GET, retried once: the release list is ~1 MB and connections drop.

    A truncated body raises ``http.client.IncompleteRead``, which is neither an
    ``OSError`` nor a ``URLError``; without catching it the check would die with
    a traceback instead of an "ask again later" message.
    """
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": bundle.USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    if token:
        # unredirected: release downloads redirect to another host, and urllib
        # would otherwise forward the token there too
        request.add_unredirected_header("Authorization", f"Bearer {token}")
    for attempt in range(ATTEMPTS):
        more = attempt + 1 < ATTEMPTS
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(), response.headers
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or not more:
                hint = " (rate limited? set GITHUB_TOKEN)" if exc.code in (403, 429) else ""
                raise UpstreamError(f"{url}: HTTP {exc.code} {exc.reason}{hint}") from exc
        except (OSError, http.client.HTTPException) as exc:
            if not more:
                raise UpstreamError(f"{url}: {exc}") from exc
        time.sleep(RETRY_DELAY)
    raise UpstreamError(f"{url}: gave up after {ATTEMPTS} attempts")  # pragma: no cover


def _get_json(url: str, token: Optional[str], timeout: float) -> Tuple[object, object]:
    body, headers = _request(url, token, timeout)
    try:
        return json.loads(body.decode("utf-8")), headers
    except (UnicodeDecodeError, ValueError) as exc:
        raise UpstreamError(f"{url}: not JSON ({exc})") from exc


def _next_page(header: object) -> Optional[str]:
    for part in str(header or "").split(","):
        match = LINK_NEXT.search(part)
        if match and match.group("rel") == "next":
            return match.group("url")
    return None


def fetch_releases(
    token: Optional[str] = None, timeout: float = 30.0, pages: int = MAX_PAGES
) -> List[dict]:
    """Every release GitHub lists for neovim/neovim, newest first."""
    releases: List[dict] = []
    url: Optional[str] = f"{API}?per_page={PAGE_SIZE}"
    for _ in range(pages):
        if not url:
            break
        payload, headers = _get_json(url, token, timeout)
        if not isinstance(payload, list):
            raise UpstreamError(f"{url}: expected a list of releases")
        releases.extend(item for item in payload if isinstance(item, dict))
        url = _next_page(headers.get("Link"))
    return releases


# --------------------------------------------------------------------------
# reading the answers
# --------------------------------------------------------------------------
def parse_tag(tag: str) -> Optional[Tuple[str, Tuple[int, int, int]]]:
    """:returns: ``("0.12", (0, 12, 6))`` for a stable Neovim tag."""
    match = TAG.match(tag or "")
    if match is None:
        return None
    major, minor = (int(part) for part in match.group("line").split("."))
    return match.group("line"), (major, minor, int(match.group("patch")))


def latest_patches(releases: List[dict]) -> Dict[str, str]:
    """release line -> newest published tag, e.g. ``{"0.12": "v0.12.6"}``."""
    newest: Dict[str, Tuple[Tuple[int, int, int], str]] = {}
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        parsed = parse_tag(release.get("tag_name") or "")
        if parsed is None:
            continue
        line, version = parsed
        if line not in newest or version > newest[line][0]:
            newest[line] = (version, release["tag_name"])
    return {line: tag for line, (_version, tag) in newest.items()}


def releases_by_tag(releases: List[dict]) -> Dict[str, dict]:
    return {
        release["tag_name"]: release
        for release in releases
        if isinstance(release.get("tag_name"), str)
    }


def published_checksum(asset: dict) -> Optional[str]:
    """The sha256 GitHub recorded for an uploaded asset, if it recorded one."""
    digest = asset.get("digest") or ""
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        return None
    return digest.split(":", 1)[1].strip().lower()


def parse_sha256sum(text: str, asset: str) -> Optional[str]:
    """Pull one archive's checksum out of a ``.sha256sum`` file's contents.

    The format is ``<sha256>  <file>``; some tools mark a binary file with
    ``*``.  A checksum for a *different* archive is never accepted -- falling
    back to it would verify the wrong download -- but a file holding nothing
    but one hash is still useful, so that case is allowed.
    """
    bare = []
    for line in (text or "").splitlines():
        parts = line.split()
        if not parts:
            continue
        match = SHA256.fullmatch(parts[0])
        if match is None:
            continue
        names = [part.lstrip("*") for part in parts[1:]]
        if not names:
            bare.append(match.group(0).lower())
        elif asset in names:
            return match.group(0).lower()
    return bare[0] if len(bare) == 1 else None


def sha256sum_url(target: Target) -> str:
    return f"{target.url}.sha256sum"


def remote_checksum_source(
    token: Optional[str] = None, timeout: float = 30.0
) -> Callable[[Target], Optional[str]]:
    """A callable that asks the release for one archive's ``.sha256sum`` file."""

    def source(target: Target) -> Optional[str]:
        body, _headers = _request(sha256sum_url(target), token, timeout)
        return parse_sha256sum(body.decode("utf-8", "replace"), target.asset)

    return source


# --------------------------------------------------------------------------
# the comparison
# --------------------------------------------------------------------------
@dataclass
class Line:
    """One supported release line: what we pin, and what upstream has."""

    key: str
    pinned: str
    newest: Optional[str]
    state: str


@dataclass
class Report:
    lines: List[Line] = field(default_factory=list)
    #: release lines upstream publishes that are newer than the newest supported
    unsupported: List[Tuple[str, str]] = field(default_factory=list)
    #: pinned values that contradict upstream (wrong checksum/size, no such asset)
    problems: List[str] = field(default_factory=list)
    #: assets whose published checksum could not be read
    unverified: List[str] = field(default_factory=list)
    #: true observations that are not errors (a build upstream has and we do not pin)
    notes: List[str] = field(default_factory=list)
    #: assets whose sha256 matched
    checked: int = 0

    @property
    def stale(self) -> List[Line]:
        return [line for line in self.lines if line.state in (OUTDATED, AHEAD, MISSING)]

    def drift(self) -> List[str]:
        found = [
            f"{line.key}: pinned {line.pinned}, Neovim publishes {line.newest}"
            for line in self.lines
            if line.state == OUTDATED
        ]
        found += [
            f"{line.key}: pinned {line.pinned}, newer than anything Neovim publishes"
            f" ({line.newest})"
            for line in self.lines
            if line.state == AHEAD
        ]
        found += [
            f"{line.key}: Neovim publishes no releases for this line"
            for line in self.lines
            if line.state == MISSING
        ]
        found += [
            f"Neovim has a newer release line {line} ({tag});"
            f" this project supports up to {RELEASE_KEYS[-1]}"
            for line, tag in self.unsupported
        ]
        return found

    def failures(self, strict: bool) -> List[str]:
        """Problems always fail; being behind upstream only fails under --strict."""
        found = list(self.problems)
        if strict:
            found += self.drift()
            found += [
                "could not verify the published sha256 of " + item for item in self.unverified
            ]
        return found


def _line_version(line: str) -> Tuple[int, ...]:
    return tuple(int(part) for part in line.split("."))


def check(
    releases: List[dict],
    *,
    checksum_source: Optional[Callable[[Target], Optional[str]]] = None,
    verify_checksums: bool = True,
) -> Report:
    """Compare the pinned table with a GitHub release listing."""
    newest = latest_patches(releases)
    by_tag = releases_by_tag(releases)
    report = Report()

    newest_supported = _line_version(RELEASE_KEYS[-1])
    for line, tag in newest.items():
        if line not in RELEASES and _line_version(line) > newest_supported:
            report.unsupported.append((line, tag))
    report.unsupported.sort(key=lambda item: _line_version(item[0]))

    known_assets = {platform.asset: platform.key for platform in PLATFORMS.values()}

    for key in RELEASE_KEYS:
        flat = RELEASES[key]
        upstream_tag = newest.get(key)
        if upstream_tag is None:
            report.lines.append(Line(key, flat.version, None, MISSING))
            continue
        report.lines.append(
            Line(key, flat.version, upstream_tag, _state(flat.version, upstream_tag))
        )
        release_json = by_tag.get(flat.version)
        if release_json is None:
            report.problems.append(
                f"{key}: Neovim has no release {flat.version}, so the pin cannot be downloaded"
            )
            continue
        _check_payload(flat, release_json, known_assets, report, checksum_source, verify_checksums)
    return report


def _state(pinned: str, upstream: str) -> str:
    if pinned == upstream:
        return OK
    ours, theirs = parse_tag(pinned), parse_tag(upstream)
    if ours is not None and theirs is not None and ours[1] > theirs[1]:
        return AHEAD
    return OUTDATED


def _check_payload(
    flat: Release,
    release_json: dict,
    known_assets: Dict[str, str],
    report: Report,
    checksum_source: Optional[Callable[[Target], Optional[str]]],
    verify_checksums: bool,
) -> None:
    """Checksums, sizes and asset coverage of one pinned release line."""
    assets = {
        asset.get("name"): asset
        for asset in release_json.get("assets") or []
        if isinstance(asset, dict)
    }
    for target in flat.targets:
        asset = assets.get(target.asset)
        if asset is None:
            report.problems.append(f"{flat.version}: Neovim publishes no {target.asset}")
            continue
        size = asset.get("size")
        if isinstance(size, int) and size != target.size:
            report.problems.append(
                f"{target.label}: pinned size {target.size}, Neovim publishes {size}"
                f" for {target.asset}"
            )
        if not verify_checksums:
            continue
        try:
            published = published_checksum(asset)
            if published is None and checksum_source is not None:
                published = checksum_source(target)
        except UpstreamError as exc:
            report.unverified.append(f"{target.asset}: {exc}")
            continue
        if published is None:
            report.unverified.append(f"{target.asset}: no published digest or .sha256sum file")
        elif published != target.sha256:
            report.problems.append(
                f"{target.label}: pinned sha256 {target.sha256}, Neovim publishes {published}"
            )
        else:
            report.checked += 1

    for asset_name, platform_key in known_assets.items():
        if asset_name in assets and platform_key not in flat.assets:
            report.notes.append(
                f"{flat.version}: Neovim publishes {asset_name}; pin it for {platform_key}"
                " or list the platform in `missing`"
            )


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare the pinned Neovim releases with what Neovim publishes.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero unless every pin is the newest patch and its checksum verifies",
    )
    parser.add_argument(
        "--no-checksums",
        action="store_true",
        help="only compare versions (skips the sha256/size safety check)",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="per-request timeout")
    parser.add_argument(
        "--token",
        help="GitHub token to lift the API rate limit (default: $GITHUB_TOKEN; prefer the "
        "variable, since a command-line argument is visible to other local users)",
    )
    args = parser.parse_args(argv)
    token = args.token or os.environ.get("GITHUB_TOKEN") or None

    try:
        releases = fetch_releases(token=token, timeout=args.timeout)
    except UpstreamError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not latest_patches(releases):
        # an empty listing would make every release "missing"; that is an API
        # answer we cannot read, not a project that is up to date
        print("error: GitHub listed no Neovim releases; the answer looks wrong", file=sys.stderr)
        return 2

    report = check(
        releases,
        checksum_source=None
        if args.no_checksums
        else remote_checksum_source(token=token, timeout=args.timeout),
        verify_checksums=not args.no_checksums,
    )

    published_lines = len(latest_patches(releases))
    print(
        f"upstream  github.com/neovim/neovim: {published_lines} release line(s),"
        f" {len(RELEASE_KEYS)} supported here"
    )
    print("")
    print("{:<8} {:<10} {:<10} {}".format("release", "pinned", "newest", "result"))
    for line in report.lines:
        print(
            "{:<8} {:<10} {:<10} {}".format(line.key, line.pinned, line.newest or "-", line.state)
        )
    print("")

    if args.no_checksums:
        print("checksums not checked (--no-checksums)")
    else:
        total = sum(len(RELEASES[key].targets) for key in RELEASE_KEYS)
        print(f"{report.checked} of {total} archive checksum(s) match what Neovim published")
    for note in report.notes:
        print(f"note: {note}")
    for item in report.unverified:
        print(f"unverified: {item}")

    failures = report.failures(args.strict)
    if failures:
        print("")
        for failure in failures:
            print(f"      - {failure}")
        print("")
        print(f"{len(failures)} problem(s); see CONTRIBUTING.md for how to bump a pin")
        return 1

    print("")
    drift = report.drift()
    if drift:
        for item in drift:
            print(f"out of date: {item}")
        print("(nothing is wrong; --strict exits non-zero while a pin is behind)")
    else:
        print("the pins are up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
