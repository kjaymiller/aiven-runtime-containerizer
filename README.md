# aiven-runtime-containerizer

Convert `image:` references in a docker-compose file into local Dockerfiles
so [Aiven Apps](https://aiven.io) can build them (it builds from a
Dockerfile in your repo, not an arbitrary registry image).

Services running on an image Aiven provides as a managed service of its own
(PostgreSQL, Valkey/Redis, OpenSearch, Kafka) are skipped by default, since
Aiven detects and provisions those natively.

## Run it with uvx

No install step needed — `uvx` pulls the tool straight from GitHub, runs it
once, and throws the environment away:

```sh
uvx --from git+https://github.com/kjaymiller/aiven-runtime-containerizer dockerize-images docker-compose.aiven.yaml
```

Pass any of the usual options after the compose file, same as a normal
install:

```sh
uvx --from git+https://github.com/kjaymiller/aiven-runtime-containerizer dockerize-images docker-compose.aiven.yaml --dry-run
uvx --from git+https://github.com/kjaymiller/aiven-runtime-containerizer dockerize-images docker-compose.aiven.yaml -s otel-collector -s jaeger
```

To pin a specific tag or commit, append a ref to the git URL:

```sh
uvx --from git+https://github.com/kjaymiller/aiven-runtime-containerizer@v0.1.0 dockerize-images docker-compose.aiven.yaml
```

### Install instead

Prefer a persistent install over `uvx`? Add it to a project/environment:

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
