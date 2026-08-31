"""End-to-end tests against the real Aiven API.

Only run via `mise run test:e2e` (or the manually-triggered e2e.yml
workflow) with AIVEN_TOKEN set -- see conftest.py and the README.
Everything else (managed-image detection, binding resolution logic,
depends_on ordering) is unit-tested with a mocked directory; this file
exists only to prove the real thing actually connects.

Scope note: the "smoke check" here is a bare TCP connect to the
resolved host/port, not a protocol-level query (no `pg_isready`,
`_cluster/health`, etc.) -- that would mean a real client library per
service type, adding dependencies and failure modes this suite can't
verify without a token. A TCP connect is enough to prove the binding
resolved a real, reachable service rather than a stale name or typo;
protocol-level checks are a reasonable follow-up, not required here.

Never print/log a credential: the raw connection password is read
directly from the API response only where a scenario needs it (never
via the shipped library's AivenService, which excludes it by design)
and never included in any assertion message.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
from click.testing import CliRunner

from aiven_runtime_containerizer.cli import main

pytestmark = pytest.mark.e2e


def _tcp_reachable(host: str, port: int, timeout: float = 10) -> bool:
    with socket.create_connection((host, port), timeout=timeout):
        return True


# ---------------------------------------------------------------------------
# Convention binding finds each real service by type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("service_type", "image", "fixture_name"),
    [
        ("pg", "postgres:16", "pg_service"),
        ("opensearch", "opensearchproject/opensearch:2", "opensearch_service"),
        ("kafka", "confluentinc/cp-kafka:7.6.0", "kafka_service"),
        ("clickhouse", "clickhouse/clickhouse-server:24", "clickhouse_service"),
    ],
)
def test_convention_binding_finds_the_real_service(
    request: pytest.FixtureRequest,
    aiven_token: str,
    aiven_project: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service_type: str,
    image: str,
    fixture_name: str,
) -> None:
    service = request.getfixturevalue(fixture_name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AIVEN_TOKEN", aiven_token)
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text(f"services:\n  db:\n    image: {image}\n")
    runner = CliRunner()

    result = runner.invoke(main, [str(compose), "--project", aiven_project])

    assert result.exit_code == 0, result.output
    assert (
        f"bound to Aiven {service_type} service '{service['service_name']}'"
        in result.output
    )
    rewritten = compose.read_text()
    assert "AIVEN_DB_HOST" in rewritten
    # Never a literal secret -- a reference the container resolves itself.
    assert "AIVEN_DB_PASSWORD: ${AIVEN_DB_PASSWORD}" in rewritten
    password = service.get("service_uri_params", {}).get("password", "")
    if password:
        assert password not in rewritten
        assert password not in result.output


# ---------------------------------------------------------------------------
# --bind override resolves against the real service too
# ---------------------------------------------------------------------------


def test_bind_flag_resolves_the_real_service(
    aiven_token: str,
    aiven_project: str,
    pg_service: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AIVEN_TOKEN", aiven_token)
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text("services:\n  db:\n    image: postgres:16\n")
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            str(compose),
            "--project",
            aiven_project,
            "--bind",
            f"db={pg_service['service_name']}",
        ],
    )

    assert result.exit_code == 0, result.output
    assert f"bound to Aiven pg service '{pg_service['service_name']}'" in result.output


# ---------------------------------------------------------------------------
# depends_on: one local + one Aiven-managed dependency
# ---------------------------------------------------------------------------


def test_depends_on_ordering_with_a_managed_dependency(
    aiven_token: str,
    aiven_project: str,
    pg_service: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AIVEN_TOKEN", aiven_token)
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text(
        "services:\n"
        "  db:\n"
        "    image: postgres:16\n"
        "  web:\n"
        "    image: myorg/web:1\n"
        "    depends_on:\n"
        "      - db\n"
    )
    runner = CliRunner()

    result = runner.invoke(main, [str(compose), "--project", aiven_project])

    assert result.exit_code == 0, result.output
    assert f"bound to Aiven pg service '{pg_service['service_name']}'" in result.output
    assert "web: image 'myorg/web:1' -> build:" in result.output


# ---------------------------------------------------------------------------
# The resolved connection is real and reachable
# ---------------------------------------------------------------------------


def test_resolved_host_and_port_are_reachable(pg_service: dict) -> None:
    params = pg_service["service_uri_params"]
    assert _tcp_reachable(params["host"], int(params["port"]))
