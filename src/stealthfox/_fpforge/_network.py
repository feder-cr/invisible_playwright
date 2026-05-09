# -*- coding: utf-8 -*-
"""Generic Bayesian network for fingerprint sampling.

A Node has:
  - name
  - parents (list of parent node names)
  - CPT: either
      * marginal (no parents): flat [{value, prob}, ...]
      * conditional: {parent_tuple: [{value, prob}, ...]}
  - OR deterministic: a classifier function `(context) -> value` (no CPT)

Sampling:
  - Nodes are topologically sorted
  - For each node, look up the conditional distribution given parent values
    already sampled in `context`, then weighted-pick
  - Deterministic nodes apply their classifier directly

Values can be ANY JSON-serializable type (int, str, dict, list, bool).
Complex values (e.g. screen joint {w, h, dpr}) are stored as dicts in the
CPT and returned as-is in the context.
"""
import json
import random
from typing import Any, Callable, Dict, List, Optional, Tuple


class Node:
    """Single Bayesian node."""

    __slots__ = ("name", "parents", "cpt", "classifier", "_marginal")

    def __init__(
        self,
        name: str,
        parents: Optional[List[str]] = None,
        cpt: Optional[Any] = None,
        classifier: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ):
        self.name = name
        self.parents = list(parents or [])
        self.cpt = cpt
        self.classifier = classifier
        # Precompute: for no-parent nodes, cpt is the marginal list
        self._marginal = cpt if not self.parents and classifier is None else None

    def sample(self, context: Dict[str, Any], rng: random.Random) -> Any:
        # Deterministic nodes don't sample
        if self.classifier is not None:
            return self.classifier(context)

        if not self.parents:
            # Marginal root
            return _weighted_pick(self._marginal, rng)

        # Conditional node: build the key from parent values
        key = _parent_key(self.parents, context)
        if key not in self.cpt:
            # Fallback: concatenate all parents' tables (uniform over union)
            # Keeps sampler from crashing if data doesn't cover some combo.
            pool = []
            for v in self.cpt.values():
                pool.extend(v)
            if not pool:
                raise ValueError(
                    f"Node {self.name!r}: no CPT entries for {self.parents}={key}"
                )
            return _weighted_pick(pool, rng)
        return _weighted_pick(self.cpt[key], rng)


class Network:
    """Collection of nodes with topological sampling."""

    def __init__(self, nodes: List[Node]):
        self.nodes = _topsort(nodes)
        self.by_name = {n.name: n for n in self.nodes}

    def sample(self, rng: random.Random) -> Dict[str, Any]:
        context: Dict[str, Any] = {}
        for node in self.nodes:
            context[node.name] = node.sample(context, rng)
        return context


# ── Helpers ─────────────────────────────────────────────────────────────

def _weighted_pick(table: List[Dict[str, Any]], rng: random.Random) -> Any:
    """`table` is a list of {value, prob} dicts. Returns one value."""
    values = [e["value"] for e in table]
    probs = [float(e["prob"]) for e in table]
    if not values:
        raise ValueError("Empty CPT entry")
    total = sum(probs)
    if total <= 0:
        return rng.choice(values)
    # Normalize to be safe (CPTs can be unnormalized)
    probs = [p / total for p in probs]
    return rng.choices(values, weights=probs, k=1)[0]


def _parent_key(parents: List[str], context: Dict[str, Any]) -> str:
    """Build a JSON-stable key from parent values in declared order."""
    if len(parents) == 1:
        v = context[parents[0]]
        return v if isinstance(v, str) else json.dumps(v, sort_keys=True)
    return json.dumps([context[p] for p in parents], sort_keys=True)


def _topsort(nodes: List[Node]) -> List[Node]:
    """Topological sort by parent-before-child."""
    by_name = {n.name: n for n in nodes}
    visited: set = set()
    order: List[Node] = []

    def visit(n: Node, path: set):
        if n.name in visited:
            return
        if n.name in path:
            raise ValueError(f"Cycle at {n.name}")
        path.add(n.name)
        for p in n.parents:
            if p not in by_name:
                raise ValueError(f"Node {n.name} has unknown parent {p}")
            visit(by_name[p], path)
        path.discard(n.name)
        visited.add(n.name)
        order.append(n)

    for n in nodes:
        visit(n, set())
    return order
