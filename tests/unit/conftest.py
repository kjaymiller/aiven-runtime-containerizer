"""Shared fixtures for the cli.py unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

# A compose file with comments, an explicit quoted string, and a service
# ordering that a naive dict-rewrite would disturb. Used to assert that a
# run touching only `web` leaves everything else byte-for-byte recognizable
# (the whole reason cli.py uses ruamel.yaml instead of PyYAML).
COMPOSE_TEXT = """\
# top-level comment describing this stack
services:
  web:
    image: myorg/web:1.2.3  # inline comment on web's image
    ports:
      - "8000:8000"

  db:
    image: postgres:16
    environment:
      POSTGRES_PASSWORD: "example"  # quoted on purpose

  already-built:
    build: ./docker/already-built

  no-image:
    depends_on:
      - db
"""


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def compose_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # `--docker-dir` defaults to the relative path "docker", resolved against
    # the process cwd -- not the compose file's directory. Chdir into
    # tmp_path so the default docker_dir/<service>/Dockerfile lands next to
    # the compose file, same as a real invocation from a project root would.
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "docker-compose.yaml"
    path.write_text(COMPOSE_TEXT)
    return path
