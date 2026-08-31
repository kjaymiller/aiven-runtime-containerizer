"""Unit tests for aiven_runtime_containerizer.cli.

No network access, no Aiven credentials -- see tests/e2e for that.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from aiven_runtime_containerizer.cli import (
    MANAGED_IMAGE_PATTERNS,
    build_dockerfile,
    main,
    managed_service_match,
)

# ---------------------------------------------------------------------------
# managed_service_match
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pattern", MANAGED_IMAGE_PATTERNS)
def test_managed_service_match_hits_every_pattern(pattern: str) -> None:
    image = f"some-registry.example.com/{pattern}:latest"
    assert managed_service_match(image) == pattern


@pytest.mark.parametrize("pattern", MANAGED_IMAGE_PATTERNS)
def test_managed_service_match_is_case_insensitive(pattern: str) -> None:
    image = f"{pattern.upper()}:16"
    assert managed_service_match(image) == pattern


def test_managed_service_match_returns_none_for_unmanaged_image() -> None:
    assert managed_service_match("myorg/web:1.2.3") is None
    assert managed_service_match("nginx:latest") is None


# ---------------------------------------------------------------------------
# build_dockerfile
# ---------------------------------------------------------------------------


def test_build_dockerfile_content() -> None:
    assert build_dockerfile("myorg/web:1.2.3") == "FROM myorg/web:1.2.3\n"


# ---------------------------------------------------------------------------
# main(): happy path
# ---------------------------------------------------------------------------


def test_default_run_converts_only_the_unmanaged_service(
    runner: CliRunner, compose_file: Path
) -> None:
    result = runner.invoke(main, [str(compose_file)])

    assert result.exit_code == 0, result.output
    docker_dir = compose_file.parent / "docker"
    assert (docker_dir / "web" / "Dockerfile").read_text() == "FROM myorg/web:1.2.3\n"
    # postgres is managed -> skipped by default, no Dockerfile written for it.
    assert not (docker_dir / "db").exists()

    rewritten = compose_file.read_text()
    assert "build: ./docker/web" in rewritten
    assert "image: myorg/web:1.2.3" not in rewritten
    # untouched services keep their image: as-is.
    assert "image: postgres:16" in rewritten


def test_default_run_reports_skipped_services(
    runner: CliRunner, compose_file: Path
) -> None:
    result = runner.invoke(main, [str(compose_file)])

    assert result.exit_code == 0, result.output
    assert "db (looks like an Aiven-managed 'postgres' service" in result.output
    assert "already-built (already has build:)" in result.output
    assert "no-image (no image:)" in result.output


# ---------------------------------------------------------------------------
# --dry-run
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(runner: CliRunner, compose_file: Path) -> None:
    original = compose_file.read_text()

    result = runner.invoke(main, [str(compose_file), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "(dry run) would write" in result.output
    assert compose_file.read_text() == original
    assert not (compose_file.parent / "docker").exists()


# ---------------------------------------------------------------------------
# --output
# ---------------------------------------------------------------------------


def test_output_writes_elsewhere_and_leaves_original_untouched(
    runner: CliRunner, compose_file: Path
) -> None:
    original = compose_file.read_text()
    out_path = compose_file.parent / "docker-compose.generated.yaml"

    result = runner.invoke(main, [str(compose_file), "-o", str(out_path)])

    assert result.exit_code == 0, result.output
    assert out_path.exists()
    assert "build: ./docker/web" in out_path.read_text()
    assert compose_file.read_text() == original


# ---------------------------------------------------------------------------
# -s / --service
# ---------------------------------------------------------------------------


def test_service_flag_restricts_conversion_to_named_services(
    runner: CliRunner, compose_file: Path
) -> None:
    result = runner.invoke(main, [str(compose_file), "-s", "web"])

    assert result.exit_code == 0, result.output
    rewritten = compose_file.read_text()
    assert "build: ./docker/web" in rewritten
    # db wasn't named, so it's still skipped even though it wasn't requested.
    assert "image: postgres:16" in rewritten


def test_service_flag_with_unknown_name_raises(
    runner: CliRunner, compose_file: Path
) -> None:
    result = runner.invoke(main, [str(compose_file), "-s", "does-not-exist"])

    assert result.exit_code != 0
    assert "Not found in" in result.output
    assert "does-not-exist" in result.output


def test_service_flag_on_managed_image_converts_without_include_managed(
    runner: CliRunner, compose_file: Path
) -> None:
    result = runner.invoke(main, [str(compose_file), "-s", "db"])

    assert result.exit_code == 0, result.output
    rewritten = compose_file.read_text()
    assert "build: ./docker/db" in rewritten
    assert "image: postgres:16" not in rewritten


# ---------------------------------------------------------------------------
# --force
# ---------------------------------------------------------------------------


def test_existing_dockerfile_is_skipped_without_force(
    runner: CliRunner, compose_file: Path
) -> None:
    docker_dir = compose_file.parent / "docker"
    web_dir = docker_dir / "web"
    web_dir.mkdir(parents=True)
    (web_dir / "Dockerfile").write_text("FROM stale-image\n")

    result = runner.invoke(main, [str(compose_file), "-s", "web"])

    assert result.exit_code == 1  # nothing converted, and -s was passed
    assert (web_dir / "Dockerfile").read_text() == "FROM stale-image\n"
    assert "exists, use --force" in result.output


def test_existing_dockerfile_is_overwritten_with_force(
    runner: CliRunner, compose_file: Path
) -> None:
    docker_dir = compose_file.parent / "docker"
    web_dir = docker_dir / "web"
    web_dir.mkdir(parents=True)
    (web_dir / "Dockerfile").write_text("FROM stale-image\n")

    result = runner.invoke(main, [str(compose_file), "-s", "web", "--force"])

    assert result.exit_code == 0, result.output
    assert (web_dir / "Dockerfile").read_text() == "FROM myorg/web:1.2.3\n"


# ---------------------------------------------------------------------------
# --include-managed
# ---------------------------------------------------------------------------


def test_include_managed_converts_managed_service_without_explicit_flag(
    runner: CliRunner, compose_file: Path
) -> None:
    result = runner.invoke(main, [str(compose_file), "--include-managed"])

    assert result.exit_code == 0, result.output
    rewritten = compose_file.read_text()
    assert "build: ./docker/db" in rewritten
    assert "build: ./docker/web" in rewritten


# ---------------------------------------------------------------------------
# No services: block
# ---------------------------------------------------------------------------


def test_no_services_block_raises(runner: CliRunner, tmp_path: Path) -> None:
    empty_compose = tmp_path / "empty.yaml"
    empty_compose.write_text("version: '3'\n")

    result = runner.invoke(main, [str(empty_compose)])

    assert result.exit_code != 0
    assert "No `services:` block found" in result.output


# ---------------------------------------------------------------------------
# Nothing to convert (all skipped, no -s)
# ---------------------------------------------------------------------------


def test_nothing_to_convert_exits_zero_without_service_flag(
    runner: CliRunner, tmp_path: Path
) -> None:
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text("services:\n  db:\n    image: postgres:16\n")

    result = runner.invoke(main, [str(compose)])

    assert result.exit_code == 0, result.output
    assert "Nothing to convert." in result.output


def test_nothing_to_convert_exits_nonzero_with_service_flag(
    runner: CliRunner, tmp_path: Path
) -> None:
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text(
        "services:\n  already-built:\n    build: ./docker/already-built\n"
    )

    result = runner.invoke(main, [str(compose), "-s", "already-built"])

    assert result.exit_code == 1
    assert "Nothing to convert." in result.output


# ---------------------------------------------------------------------------
# Compose round-trip fidelity (the whole reason for ruamel.yaml)
# ---------------------------------------------------------------------------


def test_untouched_services_keep_comments_and_quoting(
    runner: CliRunner, compose_file: Path
) -> None:
    result = runner.invoke(main, [str(compose_file), "-s", "web"])

    assert result.exit_code == 0, result.output
    rewritten = compose_file.read_text()

    assert "# top-level comment describing this stack" in rewritten
    assert 'POSTGRES_PASSWORD: "example"' in rewritten
    assert "build: ./docker/already-built" in rewritten
    assert "no-image:" in rewritten
    assert "depends_on:" in rewritten
    assert "- db" in rewritten
