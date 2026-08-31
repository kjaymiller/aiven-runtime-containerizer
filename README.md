# aiven-runtime-containerizer

Convert `image:` references in a docker-compose file into local Dockerfiles
so [Aiven Apps](https://aiven.io) can build them (it builds from a
Dockerfile in your repo, not an arbitrary registry image).

Services running on an image Aiven provides as a managed service of its own
are skipped by default, since Aiven detects and provisions those natively.
Detection is an exact match (registry host and tag ignored) against a
curated list of known image names per service type:

| Aiven service type | Example images |
| --- | --- |
| `pg` | `postgres`, `postgis`, `timescale/timescaledb`, `bitnami/postgresql` |
| `valkey` | `valkey/valkey`, `redis`, `bitnami/redis` |
| `opensearch` | `opensearchproject/opensearch`, `bitnami/opensearch` |
| `kafka` | `confluentinc/cp-kafka`, `apache/kafka`, `bitnami/kafka` |
| `clickhouse` | `clickhouse/clickhouse-server`, `bitnami/clickhouse` |

See `MANAGED_IMAGES` in `cli.py` for the full, up-to-date list.

### Binding a managed service to a real Aiven service

Pass `--project` (or set `AIVEN_PROJECT`) and a managed-looking service is
*bound* to a real, existing Aiven service instead of just skipped: its
resolved host/port/user/dbname get written into its `environment:` block.
It's still not dockerized -- Aiven runs it natively either way -- but the
compose file now has real connection details for it. Requires
`AIVEN_TOKEN` in the environment (read-only lookups only: this never
creates, modifies, or deletes a service).

Which real service it binds to:

- **By convention (default):** the one Aiven service of that type
  (`pg`/`opensearch`/`kafka`/`clickhouse`/`valkey`) in the project. If
  there's more than one, that's an error, not a guess -- pick one
  explicitly with one of the two options below.
- **`x-aiven-service:`** on the compose service, naming the Aiven service
  by name (compose ignores unknown `x-*` keys, so this is safe alongside
  a normal `docker compose up`):

  ```yaml
  services:
    db:
      image: postgres:16
      x-aiven-service: my-production-pg
  ```

- **`--bind db=my-production-pg`** on the command line -- same effect,
  without editing the compose file.

A password is never fetched or written to disk. The `environment:` block
gets a `${AIVEN_DB_PASSWORD}`-style reference instead of a literal value,
for the container to resolve at runtime from whatever the platform
injects.

```sh
dockerize-images docker-compose.aiven.yaml --project jay-miller
dockerize-images docker-compose.aiven.yaml --project jay-miller --bind db=my-pg
```

### depends_on ordering

When a run converts more than one service, they're processed in
dependency order (`depends_on`, either compose form) rather than
whatever order they happen to appear in the file -- so if `web` depends
on `worker`, `worker`'s Dockerfile exists before `web`'s is written. The
resolved order is printed (`Build order: worker -> web`), including on
`--dry-run`.

A `depends_on` naming a service that doesn't exist in `services:` is an
error. A dependency cycle is an error naming the cycle. A dependency on
an Aiven-managed service imposes no ordering constraint -- that service
is never built by this tool (it's skipped, or bound per above), so
there's nothing to build first.

### Overriding where a Dockerfile gets written

A service's generated Dockerfile normally lands in `<docker-dir>/<name>`
(`docker/web` by default). Override that for one service the same two
ways as everything else above:

- **`x-dockerfile-path:`** on the compose service:

  ```yaml
  services:
    web:
      image: myorg/web:1.0
      x-dockerfile-path: services/web
  ```

- **`--dockerfile-path web=services/web`** on the command line -- wins
  if both are given for the same service.

Either way, the file is still always named `Dockerfile`, written inside
that directory, and `build:` is rewritten to point at it -- just a
different directory than the default. The usual `--force`-gated
"already exists" skip still applies at the overridden path.

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

See [`examples/`](examples/) for runnable before/after compose files
covering all of the above -- basic conversion, managed-service skipping,
`depends_on` ordering, and service binding.

## Developing

Uses [mise](https://mise.jdx.dev) as the task runner:

```sh
mise run install  # uv sync --extra dev
mise run test     # unit tests only (pytest -m "not e2e")
mise run lint     # ruff check
mise run fmt      # ruff format
mise run check    # what CI runs: fmt check + lint + unit tests
```

`mise run test:e2e` additionally exercises the real Aiven API -- it
creates and tears down dev-tier services in a real project, so it's
opt-in, local/manual only, and not part of `check` or CI's default
workflow. It reads Aiven credentials via
[fnox](https://github.com/jdx/fnox) (reuses a global `AIVEN_TOKEN` if
you already have one set: `fnox set AIVEN_TOKEN --global`).

CI (`.github/workflows/ci.yml`) runs `mise run check` on every PR. A
separate, manually-triggered workflow (`.github/workflows/e2e.yml`,
`workflow_dispatch` only -- never on a PR event) runs the e2e suite in
GitHub Actions using an `AIVEN_TOKEN` repo secret.
