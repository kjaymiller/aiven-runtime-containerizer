"""Per-service override for where a generated Dockerfile is written.

Two ways to override the default `<docker_dir>/<name>` location, usable
together -- the CLI flag wins if both are given for the same service --
same pattern as Aiven service binding (see binding.py):

1. `x-dockerfile-path:` on the compose service.
2. `--dockerfile-path <name>=<path>` on the command line.

Either names a *directory* (the build context), consistent with how
`--docker-dir`/`build:` already work -- not a full file path with a
custom filename. The written file is still always named `Dockerfile`
inside it. Resolved relative to the current working directory, same as
`--docker-dir` itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click


class DockerfilePathError(click.ClickException):
    """A --dockerfile-path flag was malformed or repeated for one service."""


def parse_dockerfile_path_flags(flags: tuple[str, ...]) -> dict[str, str]:
    """Parse repeated `--dockerfile-path name=path` flags into a dict.

    Raises DockerfilePathError on a malformed flag or a name given more
    than once (silently taking the last one would hide a typo'd duplicate).
    """
    overrides: dict[str, str] = {}
    for raw in flags:
        name, sep, path = raw.partition("=")
        if not sep or not name or not path:
            raise DockerfilePathError(
                f"--dockerfile-path {raw!r} is not in the form <compose-service>=<path>"
            )
        if name in overrides:
            raise DockerfilePathError(
                f"--dockerfile-path {name} given more than once "
                f"({overrides[name]!r} and {path!r})"
            )
        overrides[name] = path
    return overrides


def resolve_service_dir(
    name: str,
    definition: Any,
    default_docker_dir: Path,
    override_path: str | None,
) -> Path:
    """Return the directory `name`'s generated Dockerfile should be
    written into.

    Precedence: the CLI `--dockerfile-path` override (`override_path`,
    already looked up by caller), then an `x-dockerfile-path:` key on
    `definition`, then the default `<default_docker_dir>/<name>`.
    """
    path = override_path or definition.get("x-dockerfile-path")
    if path:
        return Path(path)
    return default_docker_dir / name
