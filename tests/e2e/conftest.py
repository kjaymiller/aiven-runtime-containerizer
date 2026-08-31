"""Fixtures for the e2e suite: real, disposable dev-tier Aiven services.

Skips cleanly (not a failure) when AIVEN_TOKEN isn't set -- see the
README's "Developing" section. Never run automatically; only via
`mise run test:e2e` or the manually-triggered e2e.yml workflow.

Every service fixture here is session-scoped (created once, reused by
every test in the run) and *always* tears its service down, including
on a failed create/wait -- see `_managed_service`'s try/finally. A
crashed test run should never leave a service running.
"""

from __future__ import annotations

import os
import uuid

import pytest

from ._aiven_test_client import AivenTestClient

AIVEN_PROJECT = os.environ.get("AIVEN_PROJECT", "jay-miller")
AIVEN_CLOUD = os.environ.get("AIVEN_E2E_CLOUD", "do-nyc")
RUN_ID = uuid.uuid4().hex[:8]

# `pytestmark` here would only apply to tests defined in this file (there
# are none) -- every test module under tests/e2e/ sets its own
# `pytestmark = pytest.mark.e2e` so `pytest -m "not e2e"` deselects them.


@pytest.fixture(scope="session")
def aiven_token() -> str:
    token = os.environ.get("AIVEN_TOKEN")
    if not token:
        pytest.skip(
            "AIVEN_TOKEN not set -- run via `mise run test:e2e` with it "
            "configured (see README's Developing section) to run the e2e "
            "suite; skipping, not failing, without it."
        )
    return token


@pytest.fixture(scope="session")
def aiven_project() -> str:
    return AIVEN_PROJECT


@pytest.fixture(scope="session")
def aiven_test_client(aiven_token: str) -> AivenTestClient:
    return AivenTestClient(aiven_token)


def _managed_service(
    client: AivenTestClient,
    project: str,
    service_type: str,
    plan_env_var: str,
    default_plan: str,
):
    """Create a `service_type` service, yield its raw API info once
    RUNNING, then always delete it -- on the yielding test's success,
    its failure, or a failure to even reach RUNNING in the first place.
    """
    plan = os.environ.get(plan_env_var, default_plan)
    name = f"art-e2e-{service_type}-{RUN_ID}"
    client.create_service(project, name, service_type, plan, AIVEN_CLOUD)
    try:
        info = client.wait_until_running(project, name)
    except Exception:
        client.delete_service(project, name)
        raise
    try:
        yield info
    finally:
        client.delete_service(project, name)


@pytest.fixture(scope="session")
def pg_service(aiven_test_client: AivenTestClient, aiven_project: str):
    yield from _managed_service(
        aiven_test_client, aiven_project, "pg", "AIVEN_E2E_PG_PLAN", "hobbyist"
    )


@pytest.fixture(scope="session")
def opensearch_service(aiven_test_client: AivenTestClient, aiven_project: str):
    yield from _managed_service(
        aiven_test_client,
        aiven_project,
        "opensearch",
        "AIVEN_E2E_OPENSEARCH_PLAN",
        "startup-4",
    )


@pytest.fixture(scope="session")
def kafka_service(aiven_test_client: AivenTestClient, aiven_project: str):
    yield from _managed_service(
        aiven_test_client, aiven_project, "kafka", "AIVEN_E2E_KAFKA_PLAN", "startup-2"
    )


@pytest.fixture(scope="session")
def clickhouse_service(aiven_test_client: AivenTestClient, aiven_project: str):
    yield from _managed_service(
        aiven_test_client,
        aiven_project,
        "clickhouse",
        "AIVEN_E2E_CLICKHOUSE_PLAN",
        "hobbyist",
    )
