"""Contract checks for standalone policy and metadata behavior."""

from __future__ import annotations

import argparse
import json
import sys

import pytest

from run_standalone import (
    POLICY_RUNNERS,
    POLICY_SETS,
    _write_run_metadata,
    parse_args,
)


def test_all_policy_set_contains_every_registered_policy() -> None:
    assert set(POLICY_SETS["all"]) == set(POLICY_RUNNERS)
    assert len(POLICY_SETS["all"]) == len(POLICY_RUNNERS)


def test_omm_is_available_as_a_direct_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["run_standalone.py", "--policy", "omm"])

    assert parse_args().policy == "omm"


def test_run_metadata_omits_machine_identity(tmp_path) -> None:
    _write_run_metadata(
        reports_dir=str(tmp_path),
        run_category="test",
        args=argparse.Namespace(metadata=[]),
        extra={"scenario": "scenarios/example.yaml"},
    )

    metadata = json.loads((tmp_path / "metadata.json").read_text())
    forbidden = {"hostname", "username", "cwd", "script", "argv", "platform"}

    assert forbidden.isdisjoint(metadata)
    assert metadata["context"]["scenario"] == "scenarios/example.yaml"
