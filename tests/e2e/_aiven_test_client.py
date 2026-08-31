"""Minimal, e2e-suite-only Aiven API client for CREATING and DELETING
services -- the one place in this repo that does either.

Deliberately not part of the shipped library (`src/`): the tool itself
(`aiven_services.py`) is read-only by design, and stays that way. This
client exists only so the e2e suite can stand up and tear down its own
disposable dev-tier services for a test run, and is never imported by
`aiven_runtime_containerizer` itself.

NOTE for whoever runs this first: the exact plan slug for a "cheapest
available" plan differs per service type and can change over time. The
defaults below (`AIVEN_E2E_*_PLAN` env vars) are a best guess, not a
verified one -- if `create_service` fails with an invalid-plan error,
check `avn service plan-list --project <project> --service-type <type>`
(or the Aiven console) and override the corresponding env var.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

API_BASE = "https://api.aiven.io/v1"


class AivenApiError(RuntimeError):
    pass


class AivenTestClient:
    def __init__(self, token: str, base_url: str = API_BASE) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")

    def create_service(
        self, project: str, service_name: str, service_type: str, plan: str, cloud: str
    ) -> dict:
        return self._request(
            "POST",
            f"/project/{project}/service",
            {
                "service_name": service_name,
                "service_type": service_type,
                "plan": plan,
                "cloud": cloud,
            },
        )

    def get_service(self, project: str, service_name: str) -> dict:
        return self._request("GET", f"/project/{project}/service/{service_name}")

    def delete_service(self, project: str, service_name: str) -> None:
        self._request("DELETE", f"/project/{project}/service/{service_name}")

    def wait_until_running(
        self, project: str, service_name: str, timeout: float = 900, poll: float = 15
    ) -> dict:
        """Poll until the service reaches RUNNING, or raise TimeoutError."""
        deadline = time.monotonic() + timeout
        while True:
            info = self.get_service(project, service_name).get("service", {})
            state = info.get("state")
            if state == "RUNNING":
                return info
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"{service_name} did not reach RUNNING within {timeout}s "
                    f"(last state: {state!r})"
                )
            time.sleep(poll)

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            # Test-only diagnostic client, unlike the shipped library --
            # reading the error body here is worth the (low) risk for
            # debugging a bad plan/quota/name at setup time. Aiven error
            # bodies are just {"errors": [...], "message": ...}, no token.
            detail = exc.read().decode(errors="replace")
            raise AivenApiError(
                f"Aiven API {method} {path} failed: HTTP {exc.code}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise AivenApiError(
                f"Aiven API {method} {path} failed: {exc.reason}"
            ) from exc
