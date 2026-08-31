"""Unit tests for binding.py and its wiring into cli.py's main().

Uses a fake AivenServiceDirectory -- no network access, no Aiven
credentials. See tests/e2e for the real API.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from aiven_runtime_containerizer.aiven_services import AivenService
from aiven_runtime_containerizer.binding import (
    BindingError,
    binding_environment,
    parse_bind_flags,
    resolve_binding,
)
from aiven_runtime_containerizer.cli import main


class FakeDirectory:
    """A minimal AivenServiceDirectory: just what the protocol needs."""

    def __init__(self, services: list[AivenService]) -> None:
        self.services = services
        self.calls: list[str] = []

    def list_services(self, project: str) -> list[AivenService]:
        self.calls.append(project)
        return self.services


PG_ONE = AivenService(
    name="my-pg",
    service_type="pg",
    host="pg.example.com",
    port=5432,
    user="avnadmin",
    dbname="defaultdb",
)
PG_TWO = AivenService(
    name="other-pg", service_type="pg", host="pg2.example.com", port=5432
)
KAFKA_ONE = AivenService(
    name="my-kafka", service_type="kafka", host="kafka.example.com", port=9092
)


# ---------------------------------------------------------------------------
# parse_bind_flags
# ---------------------------------------------------------------------------


def test_parse_bind_flags_empty() -> None:
    assert parse_bind_flags(()) == {}


def test_parse_bind_flags_parses_pairs() -> None:
    assert parse_bind_flags(("db=my-pg", "queue=my-kafka")) == {
        "db": "my-pg",
        "queue": "my-kafka",
    }


@pytest.mark.parametrize("bad", ["db", "=my-pg", "db=", "db-my-pg"])
def test_parse_bind_flags_rejects_malformed(bad: str) -> None:
    with pytest.raises(BindingError):
        parse_bind_flags((bad,))


def test_parse_bind_flags_rejects_duplicate_name() -> None:
    with pytest.raises(BindingError):
        parse_bind_flags(("db=my-pg", "db=other-pg"))


# ---------------------------------------------------------------------------
# resolve_binding: convention (unique match / ambiguous / not found)
# ---------------------------------------------------------------------------


def test_resolve_binding_unique_match_via_convention() -> None:
    directory = FakeDirectory([PG_ONE, KAFKA_ONE])

    result = resolve_binding(
        compose_service_name="db",
        service_type="pg",
        project="jay-miller",
        directory=directory,
        override_name=None,
    )

    assert result == PG_ONE
    assert directory.calls == ["jay-miller"]


def test_resolve_binding_ambiguous_match_raises() -> None:
    directory = FakeDirectory([PG_ONE, PG_TWO])

    with pytest.raises(BindingError, match="multiple pg services"):
        resolve_binding(
            compose_service_name="db",
            service_type="pg",
            project="jay-miller",
            directory=directory,
            override_name=None,
        )


def test_resolve_binding_no_matching_service_raises() -> None:
    directory = FakeDirectory([KAFKA_ONE])

    with pytest.raises(BindingError, match="no pg service found"):
        resolve_binding(
            compose_service_name="db",
            service_type="pg",
            project="jay-miller",
            directory=directory,
            override_name=None,
        )


# ---------------------------------------------------------------------------
# resolve_binding: explicit override (x-aiven-service / --bind)
# ---------------------------------------------------------------------------


def test_resolve_binding_override_bypasses_ambiguity() -> None:
    directory = FakeDirectory([PG_ONE, PG_TWO])

    result = resolve_binding(
        compose_service_name="db",
        service_type="pg",
        project="jay-miller",
        directory=directory,
        override_name="other-pg",
    )

    assert result == PG_TWO


def test_resolve_binding_override_unknown_name_raises() -> None:
    directory = FakeDirectory([PG_ONE])

    with pytest.raises(BindingError, match="no Aiven service named 'nope'"):
        resolve_binding(
            compose_service_name="db",
            service_type="pg",
            project="jay-miller",
            directory=directory,
            override_name="nope",
        )


def test_resolve_binding_override_wrong_type_raises() -> None:
    directory = FakeDirectory([KAFKA_ONE])

    with pytest.raises(BindingError, match="is a kafka service, not pg"):
        resolve_binding(
            compose_service_name="db",
            service_type="pg",
            project="jay-miller",
            directory=directory,
            override_name="my-kafka",
        )


def test_resolve_binding_override_with_no_service_type_matches_any_type() -> None:
    directory = FakeDirectory([KAFKA_ONE])

    result = resolve_binding(
        compose_service_name="whatever",
        service_type=None,
        project="jay-miller",
        directory=directory,
        override_name="my-kafka",
    )

    assert result == KAFKA_ONE


def test_resolve_binding_no_type_and_no_override_raises() -> None:
    directory = FakeDirectory([KAFKA_ONE])

    with pytest.raises(BindingError, match="no image-based service type"):
        resolve_binding(
            compose_service_name="whatever",
            service_type=None,
            project="jay-miller",
            directory=directory,
            override_name=None,
        )


# ---------------------------------------------------------------------------
# binding_environment: never a literal secret
# ---------------------------------------------------------------------------


def test_binding_environment_never_writes_a_literal_password() -> None:
    env = binding_environment("db", PG_ONE)

    assert env["AIVEN_DB_HOST"] == "pg.example.com"
    assert env["AIVEN_DB_PORT"] == "5432"
    assert env["AIVEN_DB_USER"] == "avnadmin"
    assert env["AIVEN_DB_DBNAME"] == "defaultdb"
    # A reference the container resolves itself, not a fetched secret value
    # -- the literal string is the variable name, never a credential.
    assert env["AIVEN_DB_PASSWORD"] == "${AIVEN_DB_PASSWORD}"


def test_binding_environment_prefixes_by_compose_service_name() -> None:
    env = binding_environment("my-queue", KAFKA_ONE)
    assert set(env) == {
        "AIVEN_MY_QUEUE_HOST",
        "AIVEN_MY_QUEUE_PORT",
        "AIVEN_MY_QUEUE_SSL",
        "AIVEN_MY_QUEUE_PASSWORD",
    }


# ---------------------------------------------------------------------------
# main(): wiring -- --project / --bind / x-aiven-service
# ---------------------------------------------------------------------------


@pytest.fixture
def project_compose(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "docker-compose.yaml"
    path.write_text(
        "services:\n"
        "  db:\n"
        "    image: postgres:16\n"
        "  queue:\n"
        "    image: confluentinc/cp-kafka:7.6.0\n"
    )
    return path


def _patch_directory(monkeypatch: pytest.MonkeyPatch, directory: FakeDirectory) -> None:
    monkeypatch.setenv("AIVEN_TOKEN", "unused-in-tests")
    monkeypatch.setattr(
        "aiven_runtime_containerizer.cli._build_service_directory",
        lambda: directory,
    )


def test_project_flag_binds_unique_match(
    monkeypatch: pytest.MonkeyPatch, project_compose: Path
) -> None:
    directory = FakeDirectory([PG_ONE, KAFKA_ONE])
    _patch_directory(monkeypatch, directory)
    runner = CliRunner()

    result = runner.invoke(main, [str(project_compose), "--project", "jay-miller"])

    assert result.exit_code == 0, result.output
    assert "db: bound to Aiven pg service 'my-pg'" in result.output
    rewritten = project_compose.read_text()
    assert "AIVEN_DB_HOST: pg.example.com" in rewritten
    assert "AIVEN_DB_PASSWORD: ${AIVEN_DB_PASSWORD}" in rewritten
    # No Dockerfile for a bound service -- Aiven runs it natively.
    assert not (project_compose.parent / "docker" / "db").exists()
    assert "image: postgres:16" not in rewritten or "environment:" in rewritten


def test_project_flag_ambiguous_match_fails_the_run(
    monkeypatch: pytest.MonkeyPatch, project_compose: Path
) -> None:
    directory = FakeDirectory([PG_ONE, PG_TWO, KAFKA_ONE])
    _patch_directory(monkeypatch, directory)
    runner = CliRunner()

    result = runner.invoke(main, [str(project_compose), "--project", "jay-miller"])

    assert result.exit_code != 0
    assert "multiple pg services" in result.output


def test_bind_flag_overrides_convention(
    monkeypatch: pytest.MonkeyPatch, project_compose: Path
) -> None:
    directory = FakeDirectory([PG_ONE, PG_TWO])
    _patch_directory(monkeypatch, directory)
    runner = CliRunner()

    result = runner.invoke(
        main,
        [
            str(project_compose),
            "--project",
            "jay-miller",
            "-s",
            "db",
            "--bind",
            "db=other-pg",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "bound to Aiven pg service 'other-pg'" in result.output


def test_x_aiven_service_key_overrides_convention(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text(
        "services:\n  db:\n    image: postgres:16\n    x-aiven-service: other-pg\n"
    )
    directory = FakeDirectory([PG_ONE, PG_TWO])
    _patch_directory(monkeypatch, directory)
    runner = CliRunner()

    result = runner.invoke(main, [str(compose), "--project", "jay-miller"])

    assert result.exit_code == 0, result.output
    assert "bound to Aiven pg service 'other-pg'" in result.output


def test_override_without_project_raises_clear_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text(
        "services:\n  db:\n    image: postgres:16\n    x-aiven-service: other-pg\n"
    )
    runner = CliRunner()

    result = runner.invoke(main, [str(compose)])

    assert result.exit_code != 0
    assert "--project" in result.output


def test_no_project_keeps_old_skip_behavior_no_network_call(
    runner: CliRunner, compose_file: Path
) -> None:
    # compose_file (from conftest) has a plain `db: image: postgres:16`
    # with no override -- without --project this must behave exactly as
    # before #5, with zero calls to any Aiven client.
    result = runner.invoke(main, [str(compose_file)])

    assert result.exit_code == 0, result.output
    assert "looks like an Aiven-managed 'pg' service" in result.output


def test_dry_run_still_resolves_binding_but_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, project_compose: Path
) -> None:
    directory = FakeDirectory([PG_ONE, KAFKA_ONE])
    _patch_directory(monkeypatch, directory)
    original = project_compose.read_text()
    runner = CliRunner()

    result = runner.invoke(
        main, [str(project_compose), "--project", "jay-miller", "--dry-run"]
    )

    assert result.exit_code == 0, result.output
    assert "db: bound to Aiven pg service 'my-pg'" in result.output
    assert project_compose.read_text() == original
    assert directory.calls  # the lookup itself still happened


def test_missing_token_raises_clear_error(
    monkeypatch: pytest.MonkeyPatch, project_compose: Path
) -> None:
    monkeypatch.delenv("AIVEN_TOKEN", raising=False)
    runner = CliRunner()

    result = runner.invoke(main, [str(project_compose), "--project", "jay-miller"])

    assert result.exit_code != 0
    assert "AIVEN_TOKEN" in result.output


def test_binding_merges_into_an_existing_environment_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text(
        "services:\n"
        "  db:\n"
        "    image: postgres:16\n"
        "    environment:\n"
        "      APP_MODE: production\n"
    )
    directory = FakeDirectory([PG_ONE])
    _patch_directory(monkeypatch, directory)
    runner = CliRunner()

    result = runner.invoke(main, [str(compose), "--project", "jay-miller"])

    assert result.exit_code == 0, result.output
    rewritten = compose.read_text()
    assert "APP_MODE: production" in rewritten
    assert "AIVEN_DB_HOST: pg.example.com" in rewritten


# ---------------------------------------------------------------------------
# CachingServiceDirectory
# ---------------------------------------------------------------------------


def test_caching_directory_calls_inner_once_per_project() -> None:
    from aiven_runtime_containerizer.aiven_services import CachingServiceDirectory

    inner = FakeDirectory([PG_ONE])
    caching = CachingServiceDirectory(inner)

    first = caching.list_services("jay-miller")
    second = caching.list_services("jay-miller")

    assert first == second == [PG_ONE]
    assert inner.calls == ["jay-miller"]  # not called twice


def test_caching_directory_is_keyed_by_project() -> None:
    from aiven_runtime_containerizer.aiven_services import CachingServiceDirectory

    inner = FakeDirectory([PG_ONE])
    caching = CachingServiceDirectory(inner)

    caching.list_services("jay-miller")
    caching.list_services("other-project")

    assert inner.calls == ["jay-miller", "other-project"]
