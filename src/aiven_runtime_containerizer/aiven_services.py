"""Read-only lookups against the Aiven API for existing managed services.

This module never creates, modifies, or deletes anything -- it only lists
and describes services already running in an Aiven project, so `cli.py`
can bind a compose service to one of them. See `binding.py` for that.

The real HTTP-backed implementation here (`AivenApiServiceDirectory`) is
intentionally not exercised by the unit tests -- those mock the
`AivenServiceDirectory` protocol directly (see tests/unit/test_binding.py).
Its exact field-parsing against the live API is verified by the e2e suite
(#7), which is the layer that actually talks to Aiven.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

API_BASE_URL = "https://api.aiven.io/v1"


@dataclass(frozen=True)
class AivenService:
    """Just enough connection info to wire a compose service up to a real
    Aiven service -- deliberately excludes any password/URI field. This
    tool never reads or writes a credential value; the running container
    resolves `${..._PASSWORD}`-style references itself, from whatever the
    platform injects at runtime.
    """

    name: str
    service_type: str  # e.g. "pg", "kafka" -- matches MANAGED_IMAGES keys
    host: str
    port: int
    user: str | None = None
    dbname: str | None = None
    ssl: bool = True


class AivenServiceDirectory(Protocol):
    """What `binding.py` needs from an Aiven project: the list of services
    in it. A test fake only needs to implement this, not the real client.
    """

    def list_services(self, project: str) -> list[AivenService]: ...


class AivenApiError(Exception):
    """Raised for a network/auth/parsing failure talking to the Aiven API.

    Message text is built to never include the token -- callers should be
    able to `str()` this straight into a ClickException without a leak.
    """


def _parse_service(raw: dict) -> AivenService | None:
    """Build an AivenService from one entry of a `GET .../service` response.

    Best-effort and defensive: an Aiven API response has a `service_type`,
    a `service_name`, and connection details under `service_uri_params`
    for most service types. Returns None (rather than raising) for a
    service this tool doesn't recognize the shape of -- better to skip an
    unrelated service than to crash the whole lookup over it.
    """
    service_type = raw.get("service_type")
    name = raw.get("service_name")
    params = raw.get("service_uri_params") or {}
    host = params.get("host")
    port = params.get("port")
    if not (service_type and name and host and port):
        return None
    try:
        port_int = int(port)
    except (TypeError, ValueError):
        return None
    return AivenService(
        name=name,
        service_type=service_type,
        host=host,
        port=port_int,
        user=params.get("user"),
        dbname=params.get("dbname"),
        ssl=bool(params.get("sslmode", "require") != "disable"),
    )


class CachingServiceDirectory:
    """Wraps another AivenServiceDirectory, calling it at most once per
    project per run -- a compose file binding several services against
    the same project shouldn't mean a network round-trip per service.
    """

    def __init__(self, inner: AivenServiceDirectory) -> None:
        self._inner = inner
        self._cache: dict[str, list[AivenService]] = {}

    def list_services(self, project: str) -> list[AivenService]:
        if project not in self._cache:
            self._cache[project] = self._inner.list_services(project)
        return self._cache[project]


class AivenApiServiceDirectory:
    """Real, network-backed AivenServiceDirectory. Constructed with a
    bearer token -- never logged, never included in any error message.
    """

    def __init__(self, token: str, base_url: str = API_BASE_URL) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")

    def list_services(self, project: str) -> list[AivenService]:
        raw = self._get(f"/project/{project}/service")
        services = []
        for entry in raw.get("services", []):
            service = _parse_service(entry)
            if service is not None:
                services.append(service)
        return services

    def _get(self, path: str) -> dict:
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            # Deliberately not including exc.read() -- an error body from
            # this endpoint is unlikely to contain the token, but there's
            # no upside to risking it over including the detail.
            raise AivenApiError(
                f"Aiven API request to {path} failed: HTTP {exc.code}"
            ) from exc
        except urllib.error.URLError as exc:
            raise AivenApiError(
                f"Aiven API request to {path} failed: {exc.reason}"
            ) from exc
