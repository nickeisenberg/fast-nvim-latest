"""Are the pins still the Neovim releases Neovim publishes?

Two kinds of test live here:

* offline tests drive ``scripts/check_upstream.py`` with synthetic GitHub
  payloads, because a staleness check that silently passes is worse than none;
* two live tests ask GitHub for real.  Those are skipped unless
  ``FAST_NVIM_RELEASE_NETWORK_TESTS=1``: the rest of the suite is offline on
  purpose.  The scheduled ``upstream`` workflow runs the same comparison
  through ``scripts/check_upstream.py --strict``, so it needs no dev tooling
  of its own.
"""

from __future__ import annotations

import http.client
import io
import os
import re
import sys
import urllib.error
from dataclasses import replace
from pathlib import Path
from typing import List

import pytest

from fast_nvim_release.platforms import RELEASE_KEYS, RELEASES

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_upstream  # noqa: E402

TOTAL_ASSETS = sum(len(RELEASES[key].targets) for key in RELEASE_KEYS)
NETWORK = os.environ.get("FAST_NVIM_RELEASE_NETWORK_TESTS") == "1"


# --------------------------------------------------------------------------
# a GitHub answer that says "your pins are current"
# --------------------------------------------------------------------------
def payload(digests: bool = True) -> List[dict]:
    return [
        {
            "tag_name": release.version,
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": target.asset,
                    "size": target.size,
                    "digest": f"sha256:{target.sha256}" if digests else None,
                }
                for target in release.targets
            ],
        }
        for release in RELEASES.values()
    ]


def extra_release(tag: str) -> dict:
    """A release GitHub has and we do not pin; its payload is not compared."""
    return {"tag_name": tag, "draft": False, "prerelease": False, "assets": []}


def asset(data: List[dict], tag: str, name: str) -> dict:
    release = next(item for item in data if item["tag_name"] == tag)
    return next(item for item in release["assets"] if item["name"] == name)


def line(report, key: str):
    return next(item for item in report.lines if item.key == key)


# --------------------------------------------------------------------------
# reading GitHub's answer
# --------------------------------------------------------------------------
def test_a_stable_tag_is_split_into_a_line_and_a_patch():
    assert check_upstream.parse_tag("v0.12.6") == ("0.12", (0, 12, 6))
    # tags we must not treat as releases
    assert check_upstream.parse_tag("nightly") is None
    assert check_upstream.parse_tag("stable") is None
    assert check_upstream.parse_tag("v0.13") is None


def test_the_newest_patch_is_picked_numerically():
    releases = [
        extra_release("v0.10.4"),
        extra_release("v0.10.10"),
        extra_release("v0.12.6"),
        extra_release("nightly"),
    ]
    newest = check_upstream.latest_patches(releases)
    # "0.10.10" > "0.10.4" as a version, but not as a string
    assert newest["0.10"] == "v0.10.10"
    assert newest["0.12"] == "v0.12.6"
    assert "nightly" not in newest


def test_draft_and_prerelease_tags_are_ignored():
    releases = [
        {"tag_name": "v0.12.6", "draft": False, "prerelease": False},
        {"tag_name": "v0.12.7", "draft": True, "prerelease": False},
        {"tag_name": "v0.12.8", "draft": False, "prerelease": True},
    ]
    assert check_upstream.latest_patches(releases) == {"0.12": "v0.12.6"}


@pytest.mark.parametrize(
    "text, expected",
    [
        (
            "95aaa8e89473f5421114f2787c13ae0ec6e11ebbd1a13a1bd6fcf63420f8073f"
            "  nvim-linux-x86_64.tar.gz\n",
            "95aaa8e89473f5421114f2787c13ae0ec6e11ebbd1a13a1bd6fcf63420f8073f",
        ),
        (
            "95AAA8E89473F5421114F2787C13AE0EC6E11EBBD1A13A1BD6FCF63420F8073F"
            " *nvim-linux-x86_64.tar.gz\n",
            "95aaa8e89473f5421114f2787c13ae0ec6e11ebbd1a13a1bd6fcf63420f8073f",
        ),
        (
            "95aaa8e89473f5421114f2787c13ae0ec6e11ebbd1a13a1bd6fcf63420f8073f\n",
            "95aaa8e89473f5421114f2787c13ae0ec6e11ebbd1a13a1bd6fcf63420f8073f",
        ),
        # a checksum for another file is not ours
        (
            "95aaa8e89473f5421114f2787c13ae0ec6e11ebbd1a13a1bd6fcf63420f8073f"
            "  nvim-linux-arm64.tar.gz\n",
            None,
        ),
        ("", None),
    ],
)
def test_a_sha256sum_file_is_read(text, expected):
    assert check_upstream.parse_sha256sum(text, "nvim-linux-x86_64.tar.gz") == expected


def test_the_release_digest_is_used_when_github_has_one():
    sha = "a" * 64
    assert check_upstream.published_checksum({"digest": f"sha256:{sha}"}) == sha
    assert check_upstream.published_checksum({"digest": None}) is None
    assert check_upstream.published_checksum({}) is None


def test_the_next_page_link_is_read_from_the_link_header():
    header = '<https://api.test/2>; rel="next", <https://api.test/9>; rel="last"'
    assert check_upstream._next_page(header) == "https://api.test/2"
    assert check_upstream._next_page(None) is None
    assert check_upstream._next_page('<https://api.test/1>; rel="prev"') is None


def test_every_page_of_the_release_list_is_read(monkeypatch):
    calls = []
    pages = [
        ([{"tag_name": "v0.12.6"}], {"Link": '<https://api.test/2>; rel="next"'}),
        ([{"tag_name": "v0.10.4"}], {}),
    ]

    def fake_get(url, _token, _timeout):
        calls.append(url)
        return pages[len(calls) - 1]

    monkeypatch.setattr(check_upstream, "_get_json", fake_get)
    releases = check_upstream.fetch_releases()
    assert [release["tag_name"] for release in releases] == ["v0.12.6", "v0.10.4"]
    # an older release line must not fall off the end of page one
    assert calls == [f"{check_upstream.API}?per_page=100", "https://api.test/2"]


def test_a_rate_limited_answer_says_how_to_fix_it(monkeypatch):
    error = urllib.error.HTTPError(check_upstream.API, 403, "Forbidden", {}, io.BytesIO(b""))
    calls = []

    def boom(*_args, **_kwargs):
        calls.append(1)
        raise error

    monkeypatch.setattr(check_upstream.urllib.request, "urlopen", boom)
    with pytest.raises(check_upstream.UpstreamError) as caught:
        check_upstream._request(check_upstream.API, None, 1.0)
    assert "403" in str(caught.value)
    assert "GITHUB_TOKEN" in str(caught.value)
    # a rate limit does not go away by asking again, so it must not retry
    assert len(calls) == 1


class Reply:
    """A response object good enough for ``_request``."""

    def __init__(self, body: bytes):
        self.body = body
        self.headers = {}

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> Reply:
        return self

    def __exit__(self, *_exc) -> bool:
        return False


def test_a_truncated_response_is_retried_once(monkeypatch):
    """Reading the ~1 MB release list does drop connections; that must not crash."""
    monkeypatch.setattr(check_upstream.time, "sleep", lambda _seconds: None)
    replies = [http.client.IncompleteRead(b"half", 10), Reply(b'{"ok": true}')]

    def flaky(*_args, **_kwargs):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(check_upstream.urllib.request, "urlopen", flaky)
    body, _headers = check_upstream._request(check_upstream.API, None, 1.0)
    assert body == b'{"ok": true}'
    assert replies == []


def test_a_dropped_connection_twice_is_an_error_not_a_traceback(monkeypatch):
    monkeypatch.setattr(check_upstream.time, "sleep", lambda _seconds: None)

    def always(*_args, **_kwargs):
        raise http.client.IncompleteRead(b"half", 10)

    monkeypatch.setattr(check_upstream.urllib.request, "urlopen", always)
    with pytest.raises(check_upstream.UpstreamError) as caught:
        check_upstream._request(check_upstream.API, None, 1.0)
    assert "IncompleteRead" in str(caught.value)


def test_a_server_error_is_retried_too(monkeypatch):
    monkeypatch.setattr(check_upstream.time, "sleep", lambda _seconds: None)
    replies = [
        urllib.error.HTTPError(check_upstream.API, 502, "Bad Gateway", {}, io.BytesIO(b"")),
        Reply(b"[]"),
    ]

    def flaky(*_args, **_kwargs):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(check_upstream.urllib.request, "urlopen", flaky)
    body, _headers = check_upstream._request(check_upstream.API, None, 1.0)
    assert body == b"[]"


# --------------------------------------------------------------------------
# the comparison
# --------------------------------------------------------------------------
def test_current_pins_pass_every_check():
    report = check_upstream.check(payload())
    assert [item.state for item in report.lines] == [check_upstream.OK] * len(RELEASE_KEYS)
    assert report.stale == []
    assert report.unsupported == []
    assert report.problems == []
    assert report.unverified == []
    assert report.checked == TOTAL_ASSETS
    assert report.failures(strict=True) == []


def test_a_newer_patch_is_reported_but_is_not_a_failure_by_default():
    data = payload()
    data.insert(0, extra_release("v0.12.7"))
    report = check_upstream.check(data)

    stale = line(report, "0.12")
    assert (stale.pinned, stale.newest, stale.state) == (
        "v0.12.6",
        "v0.12.7",
        check_upstream.OUTDATED,
    )
    assert [item.key for item in report.stale] == ["0.12"]
    # being behind upstream is normal between releases; --strict is the gate
    assert report.failures(strict=False) == []
    assert any("v0.12.7" in problem for problem in report.failures(strict=True))


def test_a_newer_release_line_is_worth_a_strict_failure():
    data = payload()
    data.insert(0, extra_release("v0.13.0"))
    report = check_upstream.check(data)
    assert report.unsupported == [("0.13", "v0.13.0")]
    assert report.failures(strict=False) == []
    assert any("0.13" in problem for problem in report.failures(strict=True))


def test_a_pin_newer_than_upstream_is_always_a_problem(monkeypatch):
    """A typo like v0.12.9 cannot be downloaded, so it fails without --strict."""
    typo = replace(RELEASES["0.12"], version="v0.12.9")
    monkeypatch.setattr(check_upstream, "RELEASES", dict(RELEASES, **{"0.12": typo}))

    report = check_upstream.check(payload())
    assert line(report, "0.12").state == check_upstream.AHEAD
    assert report.failures(strict=False), "a pin that does not exist upstream must fail"
    assert any("v0.12.9" in problem for problem in report.problems)


def test_a_wrong_checksum_is_always_a_problem():
    data = payload()
    target = RELEASES["0.12"].target("windows-arm64")
    assert target is not None
    asset(data, "v0.12.6", target.asset)["digest"] = "sha256:" + "0" * 64

    report = check_upstream.check(data)
    assert report.checked == TOTAL_ASSETS - 1
    assert any(target.label in problem and "sha256" in problem for problem in report.problems)
    assert report.failures(strict=False), "a wrong checksum must fail without --strict"


def test_a_wrong_size_is_always_a_problem():
    data = payload()
    target = RELEASES["0.11"].target("linux-x86_64")
    assert target is not None
    asset(data, "v0.11.7", target.asset)["size"] = 1

    report = check_upstream.check(data)
    assert any(target.asset in problem and "size" in problem for problem in report.problems)


def test_an_asset_upstream_does_not_publish_is_a_problem():
    data = payload()
    target = RELEASES["0.10"].target("macos-arm64")
    assert target is not None
    release = next(item for item in data if item["tag_name"] == "v0.10.4")
    release["assets"] = [a for a in release["assets"] if a["name"] != target.asset]

    report = check_upstream.check(data)
    assert any(target.asset in problem for problem in report.problems)


def test_a_platform_upstream_has_and_we_do_not_pin_is_a_note():
    data = payload()
    # pretend the 0.10 line never listed windows-arm64, but upstream built it
    release = next(item for item in data if item["tag_name"] == "v0.10.4")
    release["assets"].append(
        {"name": "nvim-win-arm64.zip", "size": 1, "digest": "sha256:" + "0" * 64}
    )
    report = check_upstream.check(data)
    assert any("nvim-win-arm64.zip" in note for note in report.notes)
    assert report.problems == [], "a new build upstream is coverage, not a wrong pin"


def test_a_missing_digest_falls_back_to_the_sha256sum_file():
    asked = []

    def source(target):
        asked.append(target.asset)
        return target.sha256

    report = check_upstream.check(payload(digests=False), checksum_source=source)
    assert report.checked == TOTAL_ASSETS
    assert report.unverified == []
    # every target was asked, because GitHub published no digest for any of them
    assert len(asked) == TOTAL_ASSETS


def test_an_unreadable_checksum_is_reported_and_fails_strict():
    def source(_target):
        return None

    report = check_upstream.check(payload(digests=False), checksum_source=source)
    assert report.checked == 0
    assert len(report.unverified) == TOTAL_ASSETS
    assert report.failures(strict=False) == [], "unknown is not the same as wrong"
    assert report.failures(strict=True), "--strict must not pass an unverified pin"


def test_a_failed_checksum_fetch_keeps_the_reason():
    def source(_target):
        raise check_upstream.UpstreamError("HTTP 500")

    report = check_upstream.check(payload(digests=False), checksum_source=source)
    assert report.unverified and "HTTP 500" in report.unverified[0]


def test_checksums_can_be_skipped():
    data = payload()
    target = RELEASES["0.12"].target("linux-arm64")
    assert target is not None
    asset(data, "v0.12.6", target.asset)["digest"] = "sha256:" + "0" * 64

    report = check_upstream.check(data, verify_checksums=False)
    assert report.checked == 0
    assert report.unverified == []
    assert report.problems == []


# --------------------------------------------------------------------------
# the command line
# --------------------------------------------------------------------------
def run_main(monkeypatch, capsys, data: List[dict], *args: str):
    monkeypatch.setattr(check_upstream, "fetch_releases", lambda **_kwargs: data)
    code = check_upstream.main(list(args))
    return code, capsys.readouterr()


def test_the_report_says_the_pins_are_current(monkeypatch, capsys):
    code, captured = run_main(monkeypatch, capsys, payload(), "--strict")
    assert code == 0
    assert "the pins are up to date" in captured.out
    assert f"{TOTAL_ASSETS} of {TOTAL_ASSETS} archive checksum(s) match" in captured.out
    for key in RELEASE_KEYS:
        assert f"v{RELEASES[key].version.lstrip('v')}" in captured.out


def test_drift_exits_zero_unless_strict(monkeypatch, capsys):
    data = payload()
    data.insert(0, extra_release("v0.12.7"))

    code, captured = run_main(monkeypatch, capsys, data)
    assert code == 0
    assert "out of date" in captured.out and "v0.12.7" in captured.out
    assert "the pins are up to date" not in captured.out

    code, captured = run_main(monkeypatch, capsys, data, "--strict")
    assert code == 1
    assert "v0.12.7" in captured.out


def test_a_wrong_checksum_exits_one_without_strict(monkeypatch, capsys):
    data = payload()
    target = RELEASES["0.12"].target("macos-x86_64")
    assert target is not None
    asset(data, "v0.12.6", target.asset)["digest"] = "sha256:" + "0" * 64

    code, captured = run_main(monkeypatch, capsys, data)
    assert code == 1
    assert target.label in captured.out
    assert "CONTRIBUTING.md" in captured.out


def test_an_unreachable_github_exits_two(monkeypatch, capsys):
    def boom(**_kwargs):
        raise check_upstream.UpstreamError("offline")

    monkeypatch.setattr(check_upstream, "fetch_releases", boom)
    assert check_upstream.main([]) == 2
    assert "offline" in capsys.readouterr().err


def test_an_empty_release_list_is_an_error_not_a_pass(monkeypatch, capsys):
    code, captured = run_main(monkeypatch, capsys, [], "--strict")
    assert code == 2
    assert "no Neovim releases" in captured.err


# --------------------------------------------------------------------------
# wiring: something has to ask, on a schedule
# --------------------------------------------------------------------------
WORKFLOWS = SCRIPTS.parent / ".github" / "workflows"


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_a_scheduled_job_checks_the_pins():
    text = _workflow("upstream.yml")
    assert "schedule:" in text
    assert re.search(r'cron:\s*"[^"]+"', text), "a schedule without a cron line never fires"
    assert "workflow_dispatch" in text, "it must be runnable by hand too"
    assert "scripts/check_upstream.py --strict" in text


def test_the_scheduled_job_installs_nothing_it_cannot_pin():
    """It needs no tooling beyond Python, so it has no pins to keep in sync."""
    text = _workflow("upstream.yml")
    assert "pip install" not in text
    assert "pytest" not in text


def test_the_scheduled_job_stays_off_the_publish_path():
    text = _workflow("upstream.yml")
    for forbidden in ("pypi-publish", "id-token", "environment:", "twine"):
        assert forbidden not in text
    assert "contents: read" in text


# --------------------------------------------------------------------------
# the live question, opt-in (see the module docstring)
# --------------------------------------------------------------------------
live = pytest.mark.skipif(not NETWORK, reason="set FAST_NVIM_RELEASE_NETWORK_TESTS=1 to ask GitHub")


@pytest.fixture(scope="module")
def upstream_report():
    if not NETWORK:  # pragma: no cover - the tests are skipped instead
        pytest.skip("network tests are off")
    try:
        releases = check_upstream.fetch_releases(token=os.environ.get("GITHUB_TOKEN") or None)
    except check_upstream.UpstreamError as exc:  # pragma: no cover - no network here
        pytest.skip(f"GitHub is unreachable: {exc}")
    assert releases, "GitHub listed no Neovim releases"
    return check_upstream.check(
        releases,
        checksum_source=check_upstream.remote_checksum_source(
            token=os.environ.get("GITHUB_TOKEN") or None
        ),
    )


@live
def test_every_pin_is_the_newest_patch_of_its_line(upstream_report):
    assert [item.state for item in upstream_report.lines] == [check_upstream.OK] * len(
        RELEASE_KEYS
    ), (
        "Neovim published a newer release; bump RELEASES in "
        "src/fast_nvim_release/platforms.py (see CONTRIBUTING.md): "
        + ", ".join(upstream_report.drift())
    )
    # a new minor line is a decision, not an accident, but it should be visible
    assert upstream_report.unsupported == [], (
        "Neovim has a release line this project does not support: "
        + ", ".join(f"{line} ({tag})" for line, tag in upstream_report.unsupported)
    )


@live
def test_every_pinned_checksum_is_the_published_one(upstream_report):
    assert upstream_report.problems == []
    assert upstream_report.unverified == [], (
        "a pinned checksum could not be checked against Neovim's: "
        + ", ".join(upstream_report.unverified)
    )
    assert upstream_report.checked == TOTAL_ASSETS
