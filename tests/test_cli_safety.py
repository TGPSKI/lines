"""Safety checks for simulator network entry points."""

from __future__ import annotations

import sys

import pytest
from click.testing import CliRunner

from gitlab_hk_sim.cli import _is_loopback_host, cli
from run_harness import _is_loopback_url, parse_args


@pytest.mark.parametrize("host", ["localhost", "localhost.", "127.0.0.1", "::1"])
def test_loopback_hosts_are_allowed(host: str) -> None:
    assert _is_loopback_host(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "example.com", "192.0.2.1"])
def test_non_loopback_hosts_are_rejected(host: str) -> None:
    assert not _is_loopback_host(host)


def test_serve_rejects_non_loopback_bind_before_starting() -> None:
    result = CliRunner().invoke(
        cli,
        [
            "serve",
            "--scenario",
            "scenarios/mvp-active-cap.yaml",
            "--host",
            "0.0.0.0",
        ],
    )

    assert result.exit_code == 2
    assert "--allow-non-loopback" in result.output


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://[::1]:8080",
    ],
)
def test_loopback_simulator_urls_are_allowed(url: str) -> None:
    assert _is_loopback_url(url)


def test_non_http_loopback_url_is_rejected() -> None:
    assert not _is_loopback_url("file://127.0.0.1/tmp/simulator")


def test_harness_rejects_non_loopback_url_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_harness.py", "--sim-url", "https://example.com"],
    )

    with pytest.raises(SystemExit) as exc_info:
        parse_args()

    assert exc_info.value.code == 2


def test_harness_allows_explicit_non_loopback_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_harness.py",
            "--sim-url",
            "https://example.com",
            "--allow-non-loopback",
        ],
    )

    assert parse_args().allow_non_loopback
