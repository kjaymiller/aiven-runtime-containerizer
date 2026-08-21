# aiven-runtime-containerizer

Convert `image:` references in a docker-compose file into local Dockerfiles
so [Aiven Apps](https://aiven.io) can build them (it builds from a
Dockerfile in your repo, not an arbitrary registry image).

Services running on an image Aiven provides as a managed service of its own
(PostgreSQL, Valkey/Redis, OpenSearch, Kafka) are skipped by default, since
Aiven detects and provisions those natively.

## Install

Run it directly from GitHub with `uvx`, no install step needed:

```sh
uvx --from git+https://github.com/kjaymiller/aiven-runtime-containerizer dockerize-images docker-compose.aiven.yaml
```

Or install it into a project/environment:

```sh
uv pip install git+https://github.com/kjaymiller/aiven-runtime-containerizer
pip install git+https://github.com/kjaymiller/aiven-runtime-containerizer
```

## Usage

```sh
dockerize-images docker-compose.aiven.yaml
dockerize-images docker-compose.aiven.yaml --dry-run
dockerize-images docker-compose.aiven.yaml -s otel-collector -s jaeger
dockerize-images docker-compose.aiven.yaml -o docker-compose.aiven.generated.yaml
```

Run `dockerize-images --help` for all options.
