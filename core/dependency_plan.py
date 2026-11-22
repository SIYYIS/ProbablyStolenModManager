"""Read-only planning for a draft's manifest prerequisites."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict

from .scanner import InstalledMod

_MAX_NODES = 4096  # Match the save order limit.
_MAX_EDGES = 16384
_MAX_ID_LENGTH = 512
_INVALID_PREREQUISITE = "manifest.xml declares an invalid prerequisite ID (blank, too long, or contains NUL)."

WorkshopSource = Literal["share_hint", "verified_catalog", "ambiguous"]


class DependencyItem(TypedDict):
    id: str
    name: str
    state: Literal["installed", "inactive", "missing", "invalid_manifest"]
    required_by: list[str]
    workshop_id: str | None
    workshop_source: WorkshopSource | None
    manifest_error: str | None


class DependencyPlan(TypedDict):
    draft: list[str]
    added: list[str]
    items: list[DependencyItem]
    cycles: list[list[str]]
    status: Literal["review_ready", "blocked"]
    review_ready: bool
    limited: bool


def _postorder(graph: Mapping[str, Sequence[str]]) -> list[str]:
    """Visit prerequisites first, keeping root and manifest order as tie-breakers."""
    seen: set[str] = set()
    ordered: list[str] = []
    for root in graph:
        if root in seen:
            continue
        seen.add(root)
        stack = [(root, iter(graph[root]))]
        while stack:
            node, edges = stack[-1]
            child = next(edges, None)
            if child is None:
                stack.pop()
                ordered.append(node)
            elif child not in seen:
                seen.add(child)
                stack.append((child, iter(graph[child])))
    return ordered


def _dependency_order(
    graph: dict[str, list[str]], required_by: dict[str, list[str]],
) -> tuple[list[str], list[list[str]]]:
    ordered = _postorder(graph)
    # A reverse traversal in decreasing finish order identifies actual cycle
    # members without also flagging every dependent of a cycle.
    component: dict[str, str] = {}
    for root in reversed(ordered):
        if root in component:
            continue
        component[root] = root
        stack = [root]
        while stack:
            for parent in required_by[stack.pop()]:
                if parent not in component:
                    component[parent] = root
                    stack.append(parent)

    groups: dict[str, list[str]] = {}
    for mid in graph:
        groups.setdefault(component[mid], []).append(mid)
    cycles = [group for group in groups.values() if len(group) > 1 or group[0] in graph[group[0]]]
    if not cycles:
        return ordered, cycles

    # Order across cycle groups normally; only internal edges are unsatisfiable.
    condensed = {
        root: list(dict.fromkeys(
            component[pre] for mid in group for pre in graph[mid] if component[pre] != root
        ))
        for root, group in groups.items()
    }
    return [mid for root in _postorder(condensed) for mid in groups[root]], cycles


def _workshop_candidates(
    share_hints: Mapping[str, str] | None, verified_catalog: Mapping[str, str] | None,
) -> dict[str, tuple[str | None, WorkshopSource]]:
    candidates: dict[str, set[str]] = {}
    owners: dict[str, set[str]] = {}
    verified: set[str] = set()
    for entries, is_verified in ((share_hints, False), (verified_catalog, True)):
        for mid, raw in (entries or {}).items():
            if not isinstance(raw, str) or not re.fullmatch(r"[0-9]{1,20}", raw):
                continue
            item_id = raw.lstrip("0")
            if not item_id:
                continue
            candidates.setdefault(mid, set()).add(item_id)
            owners.setdefault(item_id, set()).add(mid)
            if is_verified:
                verified.add(mid)

    result: dict[str, tuple[str | None, WorkshopSource]] = {}
    for mid, ids in candidates.items():
        if len(ids) > 1:
            result[mid] = (None, "ambiguous")
            continue
        item_id = next(iter(ids))
        if len(owners[item_id]) > 1:
            result[mid] = (None, "ambiguous")
        else:
            result[mid] = (item_id, "verified_catalog" if mid in verified else "share_hint")
    return result


def plan_dependencies(
    draft: Sequence[str],
    installed: Mapping[str, InstalledMod],
    *,
    share_hints: Mapping[str, str] | None = None,
    verified_catalog: Mapping[str, str] | None = None,
) -> DependencyPlan:
    """Build a proposal against an effective scanner snapshot without side effects.

    ``installed`` and ``inactive`` mean present in or absent from the input
    draft, respectively, for a valid installed mod. Items include draft roots
    and their transitive prerequisites. ``required_by`` lists direct dependents.
    Missing or invalid roots remain in the draft, but neither is added as a
    prerequisite. ``cycles`` lists groups of members, not paths.

    Malformed prerequisite IDs are not included and invalidate the declaring
    manifest for this plan. If a graph limit is exceeded, ``limited`` is true
    and the returned partial proposal is blocked even without other blockers.

    Source maps are exact mod-ID to Workshop-ID mappings, used only for missing
    items. Pass only locally verified mappings in ``verified_catalog``. A share
    hint is not proof that a download will contain the expected mod.

    Review readiness covers dependencies in this snapshot only, not version or
    resource diagnostics, save revision checks, or authorization to write.
    """
    roots: dict[str, None] = {}
    limited = False
    for mid in draft:
        if mid in roots:
            continue
        if len(roots) == _MAX_NODES:
            limited = True
            break
        roots[mid] = None

    reachable = list(roots)
    required_by: dict[str, list[str]] = {mid: [] for mid in roots}
    graph: dict[str, list[str]] = {mid: [] for mid in roots}
    invalid_declarations: set[str] = set()
    edge_count = 0
    for mid in reachable:
        if limited:
            break
        mod = installed.get(mid)
        if not mod or mod.manifest.parse_error:
            continue
        seen: set[str] = set()
        for pre in mod.manifest.prerequisites:
            if len(pre) > _MAX_ID_LENGTH or "\0" in pre or not pre.strip():
                invalid_declarations.add(mid)
                continue
            if pre in seen:
                continue
            is_new = pre not in graph
            if edge_count == _MAX_EDGES or (is_new and len(graph) == _MAX_NODES):
                limited = True
                break
            if is_new:
                graph[pre] = []
                required_by[pre] = []
                reachable.append(pre)
            seen.add(pre)
            edge_count += 1
            graph[mid].append(pre)
            required_by[pre].append(mid)

    ordered, cycles = _dependency_order(graph, required_by)
    sources = _workshop_candidates(share_hints, verified_catalog)
    proposed: list[str] = []
    added: list[str] = []
    items: list[DependencyItem] = []
    blocked = limited or bool(cycles)
    for mid in ordered:
        mod = installed.get(mid)
        error = mod.manifest.parse_error if mod else None
        if mid in invalid_declarations:
            error = _INVALID_PREREQUISITE
        item: DependencyItem = {
            "id": mid,
            "name": mod.display_name if mod else mid,
            "state": "installed",
            "required_by": required_by[mid],
            "workshop_id": None,
            "workshop_source": None,
            "manifest_error": error,
        }
        if mod is None:
            item["state"] = "missing"
            item["workshop_id"], item["workshop_source"] = sources.get(mid, (None, None))
            blocked = True
        elif error:
            item["state"] = "invalid_manifest"
            blocked = True
        elif mid not in roots:
            item["state"] = "inactive"
            added.append(mid)
        if mid in roots or item["state"] == "inactive":
            proposed.append(mid)
        items.append(item)

    return {
        "draft": proposed,
        "added": added,
        "items": items,
        "cycles": cycles,
        "status": "blocked" if blocked else "review_ready",
        "review_ready": not blocked,
        "limited": limited,
    }
