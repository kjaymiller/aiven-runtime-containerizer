"""Unit tests for ordering.py and its wiring into cli.py's main().

No network access, no Aiven credentials.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from aiven_runtime_containerizer.cli import main, managed_service_match
from aiven_runtime_containerizer.ordering import (
    DependencyError,
    dependency_names,
    resolve_build_order,
    validate_dependencies_exist,
)


def _is_managed(image: str) -> bool:
    return bool(managed_service_match(image))


def _svc(image: str | None = "myorg/app:1", depends_on=None) -> dict:
    d: dict = {}
    if image is not None:
        d["image"] = image
    if depends_on is not None:
        d["depends_on"] = depends_on
    return d


# ---------------------------------------------------------------------------
# dependency_names: both compose forms
# ---------------------------------------------------------------------------


def test_dependency_names_list_form() -> None:
    assert dependency_names(_svc(depends_on=["a", "b"])) == ["a", "b"]


def test_dependency_names_mapping_form() -> None:
    definition = _svc(depends_on={"a": {"condition": "service_healthy"}, "b": {}})
    assert dependency_names(definition) == ["a", "b"]


def test_dependency_names_absent() -> None:
    assert dependency_names(_svc()) == []


# ---------------------------------------------------------------------------
# validate_dependencies_exist
# ---------------------------------------------------------------------------


def test_validate_dependencies_exist_passes_for_known_names() -> None:
    all_services = {"a": _svc(), "b": _svc(depends_on=["a"])}
    validate_dependencies_exist(all_services)  # no raise


def test_validate_dependencies_exist_raises_for_unknown_name() -> None:
    all_services = {"b": _svc(depends_on=["ghost"])}
    with pytest.raises(DependencyError, match="depends_on unknown service 'ghost'"):
        validate_dependencies_exist(all_services)


# ---------------------------------------------------------------------------
# resolve_build_order: linear chain, diamond, cycles
# ---------------------------------------------------------------------------


def test_resolve_build_order_linear_chain() -> None:
    all_services = {
        "c": _svc(depends_on=["b"]),
        "a": _svc(),
        "b": _svc(depends_on=["a"]),
    }
    order = resolve_build_order(["c", "a", "b"], all_services, is_managed=_is_managed)
    assert order.index("a") < order.index("b") < order.index("c")


def test_resolve_build_order_diamond() -> None:
    # d depends on b and c, both of which depend on a.
    all_services = {
        "a": _svc(),
        "b": _svc(depends_on=["a"]),
        "c": _svc(depends_on=["a"]),
        "d": _svc(depends_on=["b", "c"]),
    }
    order = resolve_build_order(
        ["a", "b", "c", "d"], all_services, is_managed=_is_managed
    )
    assert order.index("a") < order.index("b")
    assert order.index("a") < order.index("c")
    assert order.index("b") < order.index("d")
    assert order.index("c") < order.index("d")


def test_resolve_build_order_two_node_cycle_raises() -> None:
    all_services = {"a": _svc(depends_on=["b"]), "b": _svc(depends_on=["a"])}
    with pytest.raises(DependencyError, match="Circular depends_on"):
        resolve_build_order(["a", "b"], all_services, is_managed=_is_managed)


def test_resolve_build_order_three_node_cycle_raises() -> None:
    all_services = {
        "a": _svc(depends_on=["b"]),
        "b": _svc(depends_on=["c"]),
        "c": _svc(depends_on=["a"]),
    }
    with pytest.raises(DependencyError, match="Circular depends_on"):
        resolve_build_order(["a", "b", "c"], all_services, is_managed=_is_managed)


def test_resolve_build_order_drops_edges_outside_the_processing_set() -> None:
    # "b" depends on "a", but "a" isn't in the set being processed (e.g.
    # -s b was passed) -- no ordering constraint, "a" doesn't even appear.
    all_services = {"a": _svc(), "b": _svc(depends_on=["a"])}
    order = resolve_build_order(["b"], all_services, is_managed=_is_managed)
    assert order == ["b"]


# ---------------------------------------------------------------------------
# resolve_build_order: no ordering constraint on a managed dependency
# ---------------------------------------------------------------------------


def test_resolve_build_order_drops_edge_to_a_managed_service() -> None:
    # "web" depends_on "db" (postgres, managed) -- no build-order
    # constraint is needed since "db" is never built by this tool.
    all_services = {
        "db": _svc(image="postgres:16"),
        "web": _svc(depends_on=["db"]),
    }
    order = resolve_build_order(["db", "web"], all_services, is_managed=_is_managed)
    # Both still get processed; order between them is unconstrained, but
    # deterministic (input order) since no edge exists between them.
    assert set(order) == {"db", "web"}


def test_resolve_build_order_mix_of_local_and_managed_dependencies() -> None:
    # "web" depends on local "worker" (must come first) and managed "db"
    # (no ordering constraint).
    all_services = {
        "db": _svc(image="postgres:16"),
        "worker": _svc(),
        "web": _svc(depends_on=["worker", "db"]),
    }
    order = resolve_build_order(
        ["db", "worker", "web"], all_services, is_managed=_is_managed
    )
    assert order.index("worker") < order.index("web")


# ---------------------------------------------------------------------------
# main(): build order is surfaced, and actually followed
# ---------------------------------------------------------------------------


@pytest.fixture
def chained_compose(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "docker-compose.yaml"
    path.write_text(
        "services:\n"
        "  worker:\n"
        "    image: myorg/worker:1\n"
        "    depends_on:\n"
        "      - web\n"
        "  web:\n"
        "    image: myorg/web:1\n"
    )
    return path


def test_main_echoes_build_order(runner: CliRunner, chained_compose: Path) -> None:
    result = runner.invoke(main, [str(chained_compose)])

    assert result.exit_code == 0, result.output
    assert "Build order: web -> worker" in result.output


def test_main_processes_in_dependency_order(
    runner: CliRunner, chained_compose: Path
) -> None:
    result = runner.invoke(main, [str(chained_compose)])

    assert result.exit_code == 0, result.output
    web_line = next(
        line for line in result.output.splitlines() if line.startswith("web:")
    )
    worker_line = next(
        line for line in result.output.splitlines() if line.startswith("worker:")
    )
    assert result.output.index(web_line) < result.output.index(worker_line)


def test_main_raises_clear_error_for_missing_dependency(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text(
        "services:\n  web:\n    image: myorg/web:1\n    depends_on:\n      - ghost\n"
    )

    result = runner.invoke(main, [str(compose)])

    assert result.exit_code != 0
    assert "depends_on unknown service 'ghost'" in result.output


def test_main_raises_clear_error_for_a_cycle(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text(
        "services:\n"
        "  a:\n"
        "    image: myorg/a:1\n"
        "    depends_on: [b]\n"
        "  b:\n"
        "    image: myorg/b:1\n"
        "    depends_on: [a]\n"
    )

    result = runner.invoke(main, [str(compose)])

    assert result.exit_code != 0
    assert "Circular depends_on" in result.output
