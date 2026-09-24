"""Finite, repository-bound dependency graph; never schedules outside M2--M6."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .protocol import Blocked

UNITS = tuple(
    f"M{m}.{n}"
    for m, count in ((2, 3), (3, 4), (4, 3), (5, 3), (6, 3))
    for n in range(1, count + 1)
)
CLOSURES: dict[str, str] = {"M2": "M2.3", "M3": "M3.4", "M4": "M4.3", "M5": "M5.3", "M6": "M6.3"}


@dataclass(frozen=True)
class Unit:
    name: str
    text: str
    dependencies: frozenset[str]


def load(root: Path) -> tuple[Unit, ...]:
    text = (root / "docs/methodology-roadmap-m2-m6.md").read_text(encoding="utf-8")
    sections = re.findall(r"^### (M[2-6]\.\d+) [^\n]+\n(.*?)(?=^### |^## |\Z)", text, re.M | re.S)
    if tuple(name for name, _ in sections) != UNITS:
        raise Blocked("Roadmap differs from the approved 16-unit graph")
    result = []
    for name, body in sections:
        match = re.search(r"\*\*Dependências:\*\*(.*?)(?=\*\*Escopo:)", body, re.S)
        if not match:
            raise Blocked(f"Missing dependencies: {name}")
        # Expand slash lists and the en-dash ranges in the approved document.
        deps_text = match[1]
        for start, end in re.findall(r"(M[2-6]\.\d+)\u2013(M[2-6]\.\d+)", deps_text):
            deps_text += " " + " ".join(UNITS[UNITS.index(start) : UNITS.index(end) + 1])
        deps: set[str] = set(re.findall(r"M[1-6](?:\.\d+)?", deps_text))
        macro = int(name[1])
        # The approved macro order also gates otherwise separable local interfaces.
        deps.add("M1" if macro == 2 else CLOSURES[f"M{macro - 1}"])
        deps = {CLOSURES.get(dep, dep) for dep in deps}
        if name in CLOSURES.values():
            deps.update(u for u in UNITS if u.startswith(name[:2] + ".") and u != name)
        if any(
            dep != "M1" and (dep not in UNITS or UNITS.index(dep) >= UNITS.index(name))
            for dep in deps
        ):
            raise Blocked(f"Invalid dependency graph: {name}: {sorted(deps)}")
        result.append(Unit(name, body, frozenset(deps)))
    return tuple(result)


def next_unit(units: tuple[Unit, ...], closed: set[str]) -> Unit | None:
    if closed - {"M1", *UNITS}:
        raise Blocked("Unknown closed unit")
    for unit in units:
        if unit.name in closed:
            if not unit.dependencies <= closed:
                raise Blocked(f"Closed unit has open dependency: {unit.name}")
        else:
            if not unit.dependencies <= closed:
                raise Blocked(f"Dependency not CLOSED: {unit.name}")
            return unit
    return None
