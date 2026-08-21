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
(PostgreSQL, Valkey/Redis, OpenSearch, Kafka) are skipped by default --
Aiven detects and provisions those natively, so dockerizing them just adds
a build Aiven doesn't need. Pass -s/--service to convert one of those
anyway, or --include-managed to stop skipping them entirely.

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

# Substrings matched against the image name (case-insensitive) to detect
# services backed by an Aiven-managed service type. Aiven auto-detects and
# provisions these -- no Dockerfile needed, or wanted.
MANAGED_IMAGE_PATTERNS = (
    "postgres",
    "postgis",
    "valkey",
    "redis",
    "opensearch",
    "kafka",
)


def managed_service_match(image: str) -> str | None:
    """Return the matching pattern if `image` looks like an Aiven-managed service."""
    lowered = image.lower()
    for pattern in MANAGED_IMAGE_PATTERNS:
        if pattern in lowered:
            return pattern
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
        f"({', '.join(MANAGED_IMAGE_PATTERNS)}) instead of skipping them."
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
