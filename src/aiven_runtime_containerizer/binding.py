"""Resolve which real Aiven service a compose service binds to.

Two ways to pick one, usable together (see README):

1. Convention: match the compose service's managed image type (as
   returned by `managed_service_match()`) against the Aiven services of
   that type in the project. Exactly one -> bind to it. Zero or more than
   one -> a clear error, never a guess.
2. Explicit override: an `x-aiven-service:` key on the compose service, or
   an equivalent `--bind <compose-service>=<aiven-service-name>` CLI flag.
   Bypasses convention matching entirely.
"""

from __future__ import annotations

import click

from .aiven_services import AivenService, AivenServiceDirectory


class BindingError(click.ClickException):
    """A binding couldn't be resolved -- ambiguous, missing, or malformed."""


def parse_bind_flags(binds: tuple[str, ...]) -> dict[str, str]:
    """Parse repeated `--bind name=aiven-service` flags into a dict.

    Raises BindingError on a malformed flag or a name given more than once
    (silently taking the last one would hide a typo'd duplicate).
    """
    overrides: dict[str, str] = {}
    for raw in binds:
        name, sep, aiven_service_name = raw.partition("=")
        if not sep or not name or not aiven_service_name:
            raise BindingError(
                f"--bind {raw!r} is not in the form <compose-service>=<aiven-service-name>"
            )
        if name in overrides:
            raise BindingError(
                f"--bind {name} given more than once "
                f"({overrides[name]!r} and {aiven_service_name!r})"
            )
        overrides[name] = aiven_service_name
    return overrides


def resolve_binding(
    *,
    compose_service_name: str,
    service_type: str | None,
    project: str,
    directory: AivenServiceDirectory,
    override_name: str | None,
) -> AivenService:
    """Return the AivenService `compose_service_name` binds to in `project`.

    `service_type` (e.g. "pg") narrows convention matching and gives a
    clearer error when an override names a service of the wrong type.
    Pass None when there's no image-based type to go on -- e.g. an
    `x-aiven-service:` override on an image this tool doesn't recognize --
    in which case an override name is matched against any service type.

    Raises BindingError if the binding can't be resolved unambiguously.
    """
    all_services = directory.list_services(project)
    matching_type = (
        all_services
        if service_type is None
        else [s for s in all_services if s.service_type == service_type]
    )

    if override_name:
        exact = [s for s in matching_type if s.name == override_name]
        if exact:
            return exact[0]
        wrong_type = [s for s in all_services if s.name == override_name]
        if wrong_type:
            raise BindingError(
                f"{compose_service_name}: Aiven service '{override_name}' in "
                f"project '{project}' is a {wrong_type[0].service_type} service, "
                f"not {service_type}"
            )
        raise BindingError(
            f"{compose_service_name}: no Aiven service named '{override_name}' "
            f"found in project '{project}'"
        )

    if service_type is None:
        raise BindingError(
            f"{compose_service_name}: no image-based service type to match "
            "against, and no override name given -- add an `x-aiven-service:` "
            f"key or `--bind {compose_service_name}=<name>`."
        )

    if not matching_type:
        raise BindingError(
            f"{compose_service_name}: no {service_type} service found in project "
            f"'{project}'. Pick one with `--bind {compose_service_name}=<name>` or "
            f"an `x-aiven-service:` key, or check --project."
        )
    if len(matching_type) > 1:
        names = ", ".join(sorted(s.name for s in matching_type))
        raise BindingError(
            f"{compose_service_name}: multiple {service_type} services in project "
            f"'{project}' ({names}) -- disambiguate with "
            f"`--bind {compose_service_name}=<name>` or an `x-aiven-service:` key."
        )
    return matching_type[0]


def binding_environment(
    compose_service_name: str, service: AivenService
) -> dict[str, str]:
    """Environment variables to write onto `compose_service_name`'s
    `environment:` block for the Aiven service it's bound to.

    Never a literal secret: host/port/user/dbname are not secret, and the
    password is a `${..._PASSWORD}`-style reference for the container to
    resolve at runtime from whatever the platform injects -- never a
    value this tool fetched or wrote itself.
    """
    prefix = "AIVEN_" + compose_service_name.upper().replace("-", "_")
    env = {
        f"{prefix}_HOST": service.host,
        f"{prefix}_PORT": str(service.port),
        f"{prefix}_SSL": "true" if service.ssl else "false",
    }
    if service.user:
        env[f"{prefix}_USER"] = service.user
    if service.dbname:
        env[f"{prefix}_DBNAME"] = service.dbname
    env[f"{prefix}_PASSWORD"] = f"${{{prefix}_PASSWORD}}"
    return env
