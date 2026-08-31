"""Topologically sort compose services by `depends_on` for build ordering.

A service that depends_on another service being built in this run must be
processed after it. A service that depends_on an Aiven-managed service
has no such constraint -- that service is never built by this tool
(skipped or bound, see cli.py/binding.py), so there's nothing to order
against. Existence of every depends_on name is still validated regardless.
"""

from __future__ import annotations

from typing import Any

import click


class DependencyError(click.ClickException):
    """A `depends_on` name doesn't exist, or the graph has a cycle."""


def dependency_names(definition: Any) -> list[str]:
    """Return a service definition's `depends_on` names, in the order
    given, for either compose form:

        depends_on: [a, b]
        depends_on: {a: {condition: service_healthy}, b: {}}
    """
    depends_on = definition.get("depends_on")
    if not depends_on:
        return []
    if isinstance(depends_on, dict):
        return list(depends_on.keys())
    return list(depends_on)


def validate_dependencies_exist(all_services: dict[str, Any]) -> None:
    """Raise DependencyError, naming both services, if any service
    depends_on a name that isn't a key in `all_services`.
    """
    for name, definition in all_services.items():
        for dep in dependency_names(definition):
            if dep not in all_services:
                raise DependencyError(f"{name}: depends_on unknown service '{dep}'")


def resolve_build_order(
    names: list[str],
    all_services: dict[str, Any],
    is_managed: Any,
) -> list[str]:
    """Topologically sort `names` (a subset of all_services' keys, in
    their original relative order) by depends_on.

    `is_managed(image: str) -> bool` decides whether a dependency target
    is an Aiven-managed service -- an edge to one is dropped rather than
    enforced, per this module's docstring. An edge to a name outside
    `names` is dropped too: nothing being built in this run depends on
    something not being built in this run, order-wise.

    Raises DependencyError naming the cycle if `names` has one (after
    dropping managed/out-of-scope edges).
    """
    names_set = set(names)
    edges: dict[str, list[str]] = {}
    for name in names:
        kept = []
        for dep in dependency_names(all_services[name]):
            if dep not in names_set:
                continue
            dep_image = all_services[dep].get("image")
            if dep_image and is_managed(dep_image):
                continue
            kept.append(dep)
        edges[name] = kept

    order: list[str] = []
    in_progress: set[str] = set()
    done: set[str] = set()
    path: list[str] = []

    def visit(node: str) -> None:
        if node in done:
            return
        if node in in_progress:
            cycle = path[path.index(node) :] + [node]
            raise DependencyError("Circular depends_on: " + " -> ".join(cycle))
        in_progress.add(node)
        path.append(node)
        for dep in edges[node]:
            visit(dep)
        path.pop()
        in_progress.discard(node)
        done.add(node)
        order.append(node)

    for name in names:
        visit(name)

    return order
