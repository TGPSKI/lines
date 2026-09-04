"""Tests for scenario loading behavior."""

from pathlib import Path

from glab_api.scenario import load_scenario


def _base_scenario_yaml(extra: str = "") -> str:
    return f"""
project:
  id: 1001
  name: queue-lab
  path: queue-lab
  path_with_namespace: example/queue-lab
  target_head: target-001
merge_requests: []
{extra}
""".strip()


def test_operation_failure_default_is_zero(tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text(_base_scenario_yaml())

    state = load_scenario(scenario_path)

    assert state.operation_failure_config.merge_failure_rate == 0.0
    assert state.operation_failure_config.rebase_failure_rate == 0.0


def test_operation_failure_scalar_sets_both_rates(tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text(
        _base_scenario_yaml(
            """
failure_path_realism: 0.001
"""
        )
    )

    state = load_scenario(scenario_path)

    assert state.operation_failure_config.merge_failure_rate == 0.001
    assert state.operation_failure_config.rebase_failure_rate == 0.001


def test_operation_failure_dict_allows_per_operation_rates(tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text(
        _base_scenario_yaml(
            """
failure_path_realism:
  merge_failure_rate: 0.002
  rebase_failure_rate: 0.003
"""
        )
    )

    state = load_scenario(scenario_path)

    assert state.operation_failure_config.merge_failure_rate == 0.002
    assert state.operation_failure_config.rebase_failure_rate == 0.003


def test_mr_merge_failure_flat_fields(tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text(
        """
project:
  id: 1001
  name: queue-lab
  path: queue-lab
  path_with_namespace: example/queue-lab
  target_head: target-001
merge_requests:
  - id: 2001
    iid: 1
    title: problematic-mr
    state: opened
    sha: mr1-sha-001
    rebased_target_sha: target-001
    merge_failures_remaining: 2
    merge_failure_status_code: 405
    merge_failure_detail: 405 Method Not Allowed
"""
    )

    state = load_scenario(scenario_path)
    mr = state.get_mr(1)
    assert mr is not None
    assert mr.merge_failures_remaining == 2
    assert mr.merge_failure_status_code == 405
    assert mr.merge_failure_detail == "405 Method Not Allowed"


def test_mr_merge_failure_nested_always(tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenario.yaml"
    scenario_path.write_text(
        """
project:
  id: 1001
  name: queue-lab
  path: queue-lab
  path_with_namespace: example/queue-lab
  target_head: target-001
merge_requests:
  - id: 2001
    iid: 1
    title: persistent-failure-mr
    state: opened
    sha: mr1-sha-001
    rebased_target_sha: target-001
    merge_failure:
      remaining: always
      status_code: 405
      detail: Method Not Allowed
"""
    )

    state = load_scenario(scenario_path)
    mr = state.get_mr(1)
    assert mr is not None
    assert mr.merge_failures_remaining == -1
    assert mr.merge_failure_status_code == 405
    assert mr.merge_failure_detail == "Method Not Allowed"


def test_tick_seconds_defaults_and_override(tmp_path: Path) -> None:
    default_path = tmp_path / "default.yaml"
    default_path.write_text(_base_scenario_yaml())
    default_state = load_scenario(default_path)
    assert default_state.tick_seconds == 60

    override_path = tmp_path / "override.yaml"
    override_path.write_text(
        _base_scenario_yaml(
            """
tick_seconds: 82
"""
        )
    )
    override_state = load_scenario(override_path)
    assert override_state.tick_seconds == 82


def test_shipped_scenarios_declare_synthetic_provenance() -> None:
    scenario_paths = sorted(Path("scenarios").glob("*.yaml"))

    assert scenario_paths
    for scenario_path in scenario_paths:
        state = load_scenario(scenario_path)
        assert state.scenario_metadata["scenario_kind"] == "synthetic"
        assert state.scenario_metadata["provenance"] == (
            "Fully fabricated for this repository; "
            "not derived from operational logs."
        )
