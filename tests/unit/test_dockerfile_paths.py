"""Unit tests for dockerfile_paths.py and its wiring into cli.py's main().

No network access, no Aiven credentials.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from aiven_runtime_containerizer.cli import main
from aiven_runtime_containerizer.dockerfile_paths import (
    DockerfilePathError,
    parse_dockerfile_path_flags,
    resolve_service_dir,
)


def _svc(**extra) -> dict:
    return dict(extra)


# ---------------------------------------------------------------------------
# parse_dockerfile_path_flags
# ---------------------------------------------------------------------------


def test_parse_dockerfile_path_flags_empty() -> None:
    assert parse_dockerfile_path_flags(()) == {}


def test_parse_dockerfile_path_flags_parses_pairs() -> None:
    assert parse_dockerfile_path_flags(("web=services/web", "db=infra/db")) == {
        "web": "services/web",
        "db": "infra/db",
    }


@pytest.mark.parametrize("bad", ["web", "=services/web", "web=", "web-services/web"])
def test_parse_dockerfile_path_flags_rejects_malformed(bad: str) -> None:
    with pytest.raises(DockerfilePathError):
        parse_dockerfile_path_flags((bad,))


def test_parse_dockerfile_path_flags_rejects_duplicate_name() -> None:
    with pytest.raises(DockerfilePathError):
        parse_dockerfile_path_flags(("web=services/web", "web=other/web"))


# ---------------------------------------------------------------------------
# resolve_service_dir: precedence (flag > x-dockerfile-path: > default)
# ---------------------------------------------------------------------------


def test_resolve_service_dir_default() -> None:
    result = resolve_service_dir("web", _svc(), Path("docker"), None)
    assert result == Path("docker/web")


def test_resolve_service_dir_compose_key_override() -> None:
    definition = _svc(**{"x-dockerfile-path": "services/web"})
    result = resolve_service_dir("web", definition, Path("docker"), None)
    assert result == Path("services/web")


def test_resolve_service_dir_flag_override() -> None:
    result = resolve_service_dir("web", _svc(), Path("docker"), "flagged/web")
    assert result == Path("flagged/web")


def test_resolve_service_dir_flag_wins_over_compose_key() -> None:
    definition = _svc(**{"x-dockerfile-path": "from-compose/web"})
    result = resolve_service_dir("web", definition, Path("docker"), "from-flag/web")
    assert result == Path("from-flag/web")


# ---------------------------------------------------------------------------
# main(): wiring -- --dockerfile-path / x-dockerfile-path
# ---------------------------------------------------------------------------


def test_dockerfile_path_flag_overrides_default_location(
    runner: CliRunner, compose_file: Path
) -> None:
    result = runner.invoke(
        main, [str(compose_file), "-s", "web", "--dockerfile-path", "web=services/web"]
    )

    assert result.exit_code == 0, result.output
    root = compose_file.parent
    assert (
        root / "services" / "web" / "Dockerfile"
    ).read_text() == "FROM myorg/web:1.2.3\n"
    assert not (root / "docker" / "web").exists()
    rewritten = compose_file.read_text()
    assert "build: ./services/web" in rewritten


def test_x_dockerfile_path_key_overrides_default_location(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text(
        "services:\n"
        "  web:\n"
        "    image: myorg/web:1.0\n"
        "    x-dockerfile-path: services/web\n"
    )

    result = runner.invoke(main, [str(compose)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "services" / "web" / "Dockerfile").exists()
    assert "build: ./services/web" in compose.read_text()


def test_dockerfile_path_flag_wins_over_compose_key(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text(
        "services:\n"
        "  web:\n"
        "    image: myorg/web:1.0\n"
        "    x-dockerfile-path: from-compose/web\n"
    )

    result = runner.invoke(
        main, [str(compose), "--dockerfile-path", "web=from-flag/web"]
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / "from-flag" / "web" / "Dockerfile").exists()
    assert not (tmp_path / "from-compose").exists()
    assert "build: ./from-flag/web" in compose.read_text()


def test_dockerfile_path_override_still_respects_force(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text("services:\n  web:\n    image: myorg/web:1.0\n")
    stale_dir = tmp_path / "services" / "web"
    stale_dir.mkdir(parents=True)
    (stale_dir / "Dockerfile").write_text("FROM stale-image\n")

    result = runner.invoke(
        main, [str(compose), "--dockerfile-path", "web=services/web"]
    )

    assert result.exit_code == 0  # nothing converted, but no -s flag => exit 0
    assert (stale_dir / "Dockerfile").read_text() == "FROM stale-image\n"
    assert "exists, use --force" in result.output


def test_dockerfile_path_override_with_force_overwrites(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text("services:\n  web:\n    image: myorg/web:1.0\n")
    stale_dir = tmp_path / "services" / "web"
    stale_dir.mkdir(parents=True)
    (stale_dir / "Dockerfile").write_text("FROM stale-image\n")

    result = runner.invoke(
        main, [str(compose), "--dockerfile-path", "web=services/web", "--force"]
    )

    assert result.exit_code == 0, result.output
    assert (stale_dir / "Dockerfile").read_text() == "FROM myorg/web:1.0\n"


def test_dockerfile_path_malformed_flag_raises(
    runner: CliRunner, compose_file: Path
) -> None:
    result = runner.invoke(main, [str(compose_file), "--dockerfile-path", "not-valid"])

    assert result.exit_code != 0
    assert "not in the form" in result.output
