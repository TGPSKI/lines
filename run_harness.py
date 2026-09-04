#!/usr/bin/env python
"""Test harness: run real gitlab-housekeeping against the policy simulator.

This script monkeypatches the integration's dependencies (GraphQL queries,
secret reader, state) so that it talks directly to the sim server without
needing a live qontract-server, Vault, or S3.

Requirements:
    Must be run from an environment where qontract-reconcile and its dependencies
    are installed (e.g., after `uv sync` in the qontract-reconcile root).

Usage:
    # 1. Start the sim server from this repository root:
    #    PYTHONPATH=src python -m glab_api.cli serve \
    #        --scenario scenarios/mvp-active-cap.yaml
    #
    # 2. Run the harness with qontract-reconcile installed or its root supplied:
    #    python run_harness.py --qontract-reconcile-root /path/to/qontract-reconcile
    #
    # 3. After the run, advance pipelines:
    #    curl -X POST http://127.0.0.1:8080/__sim/tick
    #    Then re-run the harness to see the next reconcile cycle.

Environment:
    SIM_URL: Override sim server URL (default: http://127.0.0.1:8080)
    QONTRACT_RECONCILE_ROOT: Optional path to a qontract-reconcile checkout
"""

from __future__ import annotations

import argparse
import ipaddress
import logging
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import urlparse


def _is_loopback_url(url: str) -> bool:
    """Return whether an HTTP(S) URL targets the local machine."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.hostname
    if not host:
        return False
    if host.rstrip(".").lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run gitlab-housekeeping against the policy simulator"
    )
    parser.add_argument(
        "--sim-url",
        default=os.environ.get("SIM_URL", "http://127.0.0.1:8080"),
        help="URL of the sim server (default: http://127.0.0.1:8080)",
    )
    parser.add_argument(
        "--allow-non-loopback",
        action="store_true",
        help="Allow connecting to a non-loopback simulator URL (unsafe)",
    )
    parser.add_argument(
        "--qontract-reconcile-root",
        type=Path,
        default=os.environ.get("QONTRACT_RECONCILE_ROOT"),
        help=(
            "Path to a qontract-reconcile checkout"
            " (default: $QONTRACT_RECONCILE_ROOT or installed package)"
        ),
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Run in dry-run mode (default)",
    )
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="Run with mutations enabled (rebase/merge will hit the sim)",
    )
    parser.add_argument(
        "--wait-for-pipeline",
        action="store_true",
        default=False,
        help="Pass --wait-for-pipeline to housekeeping",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=8,
        help="Housekeeping merge/rebase limit (default: 8)",
    )
    parser.add_argument(
        "--rebase",
        action="store_true",
        default=True,
        help="Enable rebase behavior (default: true)",
    )
    parser.add_argument(
        "--no-rebase",
        dest="rebase",
        action="store_false",
        help="Disable rebase",
    )
    args = parser.parse_args()
    if not args.allow_non_loopback and not _is_loopback_url(args.sim_url):
        parser.error(
            "--sim-url must use localhost or a loopback IP;"
            " pass --allow-non-loopback to override"
        )
    return args


class FakeSecretReader:
    """Returns a fixed token for any secret path."""

    def __init__(self, token: str = "sim-token"):
        self._token = token

    def read(self, secret_ref: Mapping | Any) -> str:
        return self._token

    def _read(
        self, path: str, field: str, format: str | None, version: int | None
    ) -> str:
        return self._token

    def _read_all(
        self, path: str, field: str, format: str | None, version: int | None
    ) -> dict:
        return {"token": self._token}


class FakeState:
    """In-memory state store (no S3 needed)."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def exists(self, key: str) -> bool:
        return key in self._data

    def add(self, key: str, value: Any = None, force: bool = False) -> None:
        self._data[key] = value

    def rm(self, key: str) -> None:
        self._data.pop(key, None)

    def ls(self) -> list[str]:
        return list(self._data.keys())

    def cleanup(self) -> None:
        pass

    def __enter__(self) -> FakeState:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


def build_fake_instance(sim_url: str) -> dict[str, Any]:
    """Build a fake GitLab instance dict pointing at the sim."""
    return {
        "url": sim_url,
        "token": {
            "path": "sim/token",
            "field": "token",
            "version": None,
            "format": None,
        },
        "sslVerify": False,
        "managedGroups": ["example"],
        "projectRequests": None,
    }


def build_fake_settings() -> dict[str, Any]:
    """Return minimal synthetic integration settings."""
    return {
        "repoUrl": "https://example.com/queue-lab",
        "vault": False,
        "kubeBinary": "oc",
        "mergeRequestGateway": "gitlab",
        "hashLength": 24,
    }


def build_fake_repos(sim_url: str, limit: int, rebase: bool) -> list[dict[str, Any]]:
    """Return a single fake repo pointing at the sim project."""
    return [
        {
            "url": f"{sim_url}/example/queue-lab",
            "housekeeping": {
                "enabled": True,
                "days_interval": 15,
                "limit": limit,
                "enable_closing": False,
                "rebase": rebase,
                "pipeline_timeout": None,
                "labels_allowed": None,
                "must_pass": None,
            },
        }
    ]


def run_harness(args: argparse.Namespace) -> None:
    """Patch dependencies and run the real integration."""
    if args.qontract_reconcile_root:
        reconcile_root = args.qontract_reconcile_root.resolve()
        if not (reconcile_root / "reconcile").is_dir():
            raise SystemExit(
                "qontract-reconcile root must contain the reconcile package: "
                f"{reconcile_root}"
            )
        sys.path.insert(0, str(reconcile_root))

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger("sim-harness")

    sim_url = args.sim_url.rstrip("/")
    log.info(f"Sim server: {sim_url}")
    log.info(f"Dry run: {args.dry_run}")
    log.info(f"Limit: {args.limit}")
    log.info(f"Rebase: {args.rebase}")

    instance = build_fake_instance(sim_url)
    settings = build_fake_settings()
    repos = build_fake_repos(sim_url, limit=args.limit, rebase=args.rebase)
    _secret_reader = FakeSecretReader()  # noqa: F841

    # Import here so patches apply before the module's internals resolve
    import reconcile.queries
    import reconcile.utils.gitlab_api
    import reconcile.utils.sharding
    import reconcile.utils.state

    patches = []

    # Patch query functions
    p1 = patch.object(reconcile.queries, "get_gitlab_instance", return_value=instance)
    p2 = patch.object(
        reconcile.queries, "get_app_interface_settings", return_value=settings
    )
    p3 = patch.object(
        reconcile.queries, "get_repos_gitlab_housekeeping", return_value=repos
    )
    patches.extend([p1, p2, p3])

    # Patch state init to return our in-memory state
    p4 = patch.object(reconcile.utils.state, "init_state", return_value=FakeState())
    patches.append(p4)

    # Patch sharding to always accept
    p5 = patch.object(reconcile.utils.sharding, "is_in_shard", return_value=True)
    patches.append(p5)

    # Patch GitLabApi to use our FakeSecretReader and skip instrumented session

    def patched_gitlab_api_init(
        self,
        instance_arg,
        project_id=None,
        settings=None,
        secret_reader=None,
        project_url=None,
        timeout=30,
        session=None,
    ):
        from urllib.parse import urlparse

        import gitlab as gl_module

        self.server = instance_arg["url"]
        token = FakeSecretReader().read(instance_arg["token"])
        self.ssl_verify = False

        from requests import Session as ReqSession

        self.session = session or ReqSession()

        self.gl = gl_module.Gitlab(
            self.server,
            private_token=token,
            ssl_verify=False,
            timeout=timeout,
            session=self.session,
            per_page=100,
        )
        self.gl.auth()
        assert self.gl.user
        self.user = self.gl.user

        if project_id is None and project_url is not None:
            parsed = urlparse(project_url)
            name_with_namespace = parsed.path.strip("/")
            self.project = self.gl.projects.get(name_with_namespace)
        elif project_id is not None:
            self.project = self.gl.projects.get(project_id)

    p6 = patch.object(
        reconcile.utils.gitlab_api.GitLabApi, "__init__", patched_gitlab_api_init
    )
    p7 = patch.object(
        reconcile.utils.gitlab_api.GitLabApi, "__enter__", lambda self: self
    )
    p8 = patch.object(
        reconcile.utils.gitlab_api.GitLabApi, "__exit__", lambda self, *a: None
    )
    patches.extend([p6, p7, p8])

    # Start all patches
    for p in patches:
        p.start()

    try:
        log.info("=" * 60)
        log.info("Starting gitlab-housekeeping run against sim")
        log.info("=" * 60)

        import reconcile.gitlab_housekeeping as hk_module

        hk_module.run(dry_run=args.dry_run, wait_for_pipeline=args.wait_for_pipeline)

        log.info("=" * 60)
        log.info("Run complete")
        log.info("=" * 60)

    except Exception:
        log.exception("Integration run failed")
        sys.exit(1)
    finally:
        for p in reversed(patches):
            p.stop()


if __name__ == "__main__":
    args = parse_args()
    run_harness(args)
