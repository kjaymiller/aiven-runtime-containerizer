"""Convert `image:` references in a compose file into local Dockerfiles.

Why ruamel.yaml instead of PyYAML:
    This script rewrites one key (`image:` -> `build:`) per service and
    must leave everything else in the compose file untouched -- comments,
    key order, quoting, blank lines. PyYAML's load/dump round-trip
    discards all of that, which would turn a one-line change into a
    full-file reformat. ruamel.yaml's CommentedMap-based `YAML()` API
    preserves it, which is what `preserve_quotes`, `indent()`, and
    `.insert()` below are for.

    Known tradeoff: ruamel.yaml does not treat its API as stable across
    versions -- e.g. the old functional API (`round_trip_load`/
    `round_trip_dump`) was deprecated for years then removed outright in
    0.18.0. The `>=0.18` dependency pin above has no upper bound, so a
    future release could change `.indent()` defaults, `.insert()`'s
    signature, or quote/flow-style preservation without warning. Revisit
    this pin (or bound it, e.g. `>=0.18,<0.19`) if this script starts
    misbehaving after a `uv run` picks up a newer ruamel.yaml.

Aiven Apps builds services from a Dockerfile in your repo -- it won't pull
an arbitrary image straight from a registry. This script takes a compose
file, finds every service pinned with `image:`, writes a one-line
`FROM <image>` Dockerfile for it under a docker/<service>/ directory, and
rewrites the compose file to `build:` that directory instead.

Services running on an image Aiven provides as a managed service of its own
(PostgreSQL, Valkey/Redis, OpenSearch, Kafka, ClickHouse) are skipped by
default -- Aiven detects and provisions those natively, so dockerizing them
just adds a build Aiven doesn't need. Pass -s/--service to convert one of
those anyway, or --include-managed to stop skipping them entirely.

Usage:
    dockerize-images docker-compose.aiven.yaml
    dockerize-images docker-compose.aiven.yaml --dry-run
    dockerize-images docker-compose.aiven.yaml -s otel-collector -s jaeger
    dockerize-images docker-compose.aiven.yaml -o docker-compose.aiven.generated.yaml

Or run without installing:
    uvx --from git+https://github.com/kjaymiller/aiven-runtime-containerizer dockerize-images docker-compose.aiven.yaml
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from ruamel.yaml import YAML

# Known image repo names, per Aiven-managed service type, that this tool
# recognizes as "don't dockerize this -- Aiven provisions it natively".
# Hand-curated and exact-match (against the repo name only, tag/digest and
# registry host stripped) rather than a loose substring, so an unrelated
# image that happens to contain "kafka" or "postgres" in its name (a
# `kafka-ui` sidecar, a `my-postgres-app` image) doesn't get caught by
# accident. Add new known image names here as they come up.
MANAGED_IMAGES: dict[str, tuple[str, ...]] = {
    "pg": (
        "postgres",
        "postgis",
        "timescale/timescaledb",
        "timescale/timescaledb-ha",
        "bitnami/postgresql",
    ),
    "valkey": (
        "valkey/valkey",
        "valkey",
        "redis",
        "bitnami/redis",
        "bitnami/valkey",
    ),
    "opensearch": (
        "opensearchproject/opensearch",
        "bitnami/opensearch",
    ),
    "kafka": (
        "confluentinc/cp-kafka",
        "confluentinc/cp-server",
        "apache/kafka",
        "bitnami/kafka",
    ),
    "clickhouse": (
        "clickhouse/clickhouse-server",
        "yandex/clickhouse-server",
        "bitnami/clickhouse",
    ),
}


def _image_repo(image: str) -> str:
    """Return `image`'s repository name, lowercased, with tag/digest and any
    registry host stripped (e.g. `docker.io/library/postgres:16` -> `postgres`,
    `ghcr.io/foo/clickhouse-server:latest` -> `foo/clickhouse-server`).
    """
    repo = image.split("@", 1)[0]  # drop a digest, if any
    if "/" in repo:
        head, _, tail = repo.rpartition("/")
        repo = f"{head}/{tail.split(':', 1)[0]}"
    else:
        repo = repo.split(":", 1)[0]  # no "/", so no registry port to confuse this

    parts = repo.split("/")
    # A leading component containing "." or ":" (or literally "localhost") is
    # a registry host per the Docker image reference spec, not a namespace --
    # drop it so `docker.io/library/postgres` and `postgres` match the same way.
    if len(parts) > 1 and (
        "." in parts[0] or ":" in parts[0] or parts[0] == "localhost"
    ):
        parts = parts[1:]
    return "/".join(parts).lower()


def managed_service_match(image: str) -> str | None:
    """Return the Aiven service type `image` looks like (e.g. "pg", "kafka"),
    or None if it doesn't match any known managed-service image.

    A match requires the *whole* repo name or its basename (the part after
    the last "/") to equal a curated entry or its basename -- not a
    substring -- so `kafka-ui` or `my-postgres-app` don't get caught by a
    real `kafka`/`postgres` image's name appearing inside them.
    """
    repo = _image_repo(image)
    basename = repo.rsplit("/", 1)[-1]
    for service_type, known_images in MANAGED_IMAGES.items():
        for known in known_images:
            if repo == known or basename == known.rsplit("/", 1)[-1]:
                return service_type
    return None


def build_dockerfile(image: str) -> str:
    return f"FROM {image}\n"


@click.command()
@click.argument(
    "compose_file", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option(
    "-o",
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the rewritten compose file here instead of editing in place.",
)
@click.option(
    "--docker-dir",
    "docker_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("docker"),
    show_default=True,
    help="Directory to hold the generated docker/<service>/Dockerfile files.",
)
@click.option(
    "-s",
    "--service",
    "services",
    multiple=True,
    help="Only convert this service (repeatable). Default: every service with an `image:`.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite a Dockerfile that already exists for a service.",
)
@click.option(
    "--include-managed",
    is_flag=True,
    help=(
        "Also convert services on an Aiven-managed image "
        f"({', '.join(MANAGED_IMAGES)}) instead of skipping them."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would change without writing any files.",
)
def main(
    compose_file: Path,
    output_path: Path | None,
    docker_dir: Path,
    services: tuple[str, ...],
    force: bool,
    include_managed: bool,
    dry_run: bool,
) -> None:
    """Convert IMAGE references in COMPOSE_FILE to local build: Dockerfiles."""
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    # Matches the compose-file convention already in this repo: list items
    # indented two spaces past their key, not flush with it.
    yaml.indent(mapping=2, sequence=4, offset=2)

    compose = yaml.load(compose_file)
    all_services = compose.get("services") or {}
    if not all_services:
        raise click.ClickException(f"No `services:` block found in {compose_file}")

    wanted = set(services) if services else None
    if wanted:
        missing = wanted - set(all_services)
        if missing:
            raise click.ClickException(
                f"Not found in {compose_file}: {', '.join(sorted(missing))}"
            )

    converted: list[str] = []
    skipped: list[str] = []

    for name, definition in all_services.items():
        if wanted and name not in wanted:
            continue
        if "build" in definition:
            skipped.append(f"{name} (already has build:)")
            continue
        image = definition.get("image")
        if not image:
            skipped.append(f"{name} (no image:)")
            continue

        explicitly_requested = name in (wanted or set())
        managed_match = managed_service_match(image)
        if managed_match and not include_managed and not explicitly_requested:
            skipped.append(
                f"{name} (looks like an Aiven-managed '{managed_match}' service, "
                "use -s or --include-managed to convert anyway)"
            )
            continue

        service_dir = docker_dir / name
        dockerfile_path = service_dir / "Dockerfile"
        content = build_dockerfile(image)

        if dockerfile_path.exists() and not force:
            skipped.append(f"{name} ({dockerfile_path} exists, use --force)")
            continue

        click.echo(f"{name}: image {image!r} -> build: ./{service_dir.as_posix()}")
        if not dry_run:
            service_dir.mkdir(parents=True, exist_ok=True)
            dockerfile_path.write_text(content)

        del definition["image"]
        definition.insert(0, "build", f"./{service_dir.as_posix()}")
        converted.append(name)

    if not converted:
        click.echo("Nothing to convert.", err=True)
        if skipped:
            click.echo("Skipped: " + "; ".join(skipped), err=True)
        sys.exit(0 if not services else 1)

    dest = output_path or compose_file
    if dry_run:
        click.echo(f"(dry run) would write {dest}")
    else:
        with dest.open("w") as f:
            yaml.dump(compose, f)
        click.echo(f"Wrote {dest}")

    if skipped:
        click.echo("Skipped: " + "; ".join(skipped), err=True)


if __name__ == "__main__":
    main()
