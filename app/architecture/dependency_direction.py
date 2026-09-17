"""Dependency-direction checker.

Reads `config/module-map.json` and enforces the ordering rules from
`docs/superpowers/plans/2026-08-19-maplab-architectural-uplift-plan.md` §5.5 as a
hard, mechanical CI gate -- not documentation. Shape follows
`tests/providers/test_adr0035_ask_import_boundary.py`: a plain AST walk over each
file's `Import`/`ImportFrom` nodes matched against a forbidden-name set, extended
to read the forbidden set (and the module-to-layer mapping) from the module map
instead of a hardcoded ``ROOTS``/``FORBIDDEN`` pair.

Rules enforced:
  1.        Ingress modules must not import a State-assigned module directly.
  2-fastapi Business modules must not import ``fastapi`` -- directly, or via
            ``app.auth.jwt`` (the FastAPI-coupled verifier submodule; it
            imports ``Depends``/``HTTPException``/``Request``/``status``/
            ``HTTPAuthorizationCredentials``/``HTTPBearer`` at module scope).
            ``app.auth`` (the package re-export) is NOT in this set --
            `app/auth/__init__.py` was moved to a transport-free identity facade
            (`Principal`, `RecordAccess`, `ScopeValues`, `ResourceGrant` only;
            no import of `app.auth.jwt` or anything else that carries FastAPI),
            and the stronger, more accurate regression guard for that fact is
            the subprocess import probe
            `tests/architecture/test_principal_transport_free.py` (this
            AST-based checker can only ever prove "the name `app.auth`
            appears in an import statement", never "does `app.auth` actually
            load FastAPI" -- the probe proves the real thing by running a
            fresh interpreter). See docs/superpowers/seam-consumer-map.md
            Seam 6 for the historical import census.
  2-state   Business modules must not import a State-assigned module directly.
  3-ingress Business modules must not import an Ingress-assigned module.
            The live baseline is empty and stays empty.
  7         No production module (ingress/business/state/composition_root)
            imports a control-plane package: ``app.experiments`` or
            ``app.eval``. The match is on dotted components, so ``app.eval``
            matches ``app.eval.sql`` but not ``app.evaluation``.

Two independent staleness mechanisms, both stored in
`config/dependency-violations-baseline.json`:

  - ``violations``: an EXACT-MATCH baseline. A live violation not listed here
    is new debt (FAIL). A listed entry no longer found in code is a stale
    baseline entry (FAIL) -- remove it in the same change that fixes the code.
    Identity is ``(rule, importer, imported)``; the ``importer``/``imported``
    fields carry line numbers for humans but the *comparison* is line-number
    independent so an unrelated reformat can't manufacture false staleness.
  - ``counters``: NON-INCREASING baselines. The live count may drop (progress)
    but must never exceed the recorded number.

Two additional hard gates close ungated-input holes:

  - ``unmapped_app_files``: every ``app/**/*.py`` path must resolve to a
    layer via `config/module-map.json`. An unmapped path silently
    skips every rule above (its layer is `None`, which is never in
    `PRODUCTION_LAYERS`) -- a new file could import FastAPI, a State adapter,
    AND a control-plane package and still show zero violations. `scripts/`
    stays best-effort (tooling/control-plane churn, not part of the
    production request path this checker protects).
  - ``module_map_violations_without_enforcement_gap``: every entry in
    `module-map.json`'s own `violations[]` array must either correspond to a
    baseline entry (by importer file + rule family) or carry
    ``"status": "documented-not-enforced"`` with a reason -- so the
    human-audited map and the machine-enforced baseline can't silently
    diverge (module-map.json is an input to this checker, not itself
    verified by it, otherwise).

Usage:
    python scripts/gates/check_dependency_direction.py
    python scripts/gates/check_dependency_direction.py --show-counts  # counters, exit 0
    python scripts/gates/check_dependency_direction.py --show-cycles  # SCCs, exit 0
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


def repo_root(start: Path | None = None) -> Path:
    here = (start or Path(__file__).resolve()).parent
    for candidate in [here, *here.parents]:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError("pyproject.toml not found")


REPO_ROOT = repo_root()
DEFAULT_MODULE_MAP = REPO_ROOT / "config/module-map.json"
DEFAULT_BASELINE = REPO_ROOT / "config/dependency-violations-baseline.json"
DEFAULT_CYCLES_BASELINE = REPO_ROOT / "docs/superpowers/import-cycles-baseline.json"

PRODUCTION_LAYERS = {"ingress", "business", "state", "composition_root"}
CONTROL_PLANE_PREFIXES = ("app.experiments", "app.eval")
# `app.auth.jwt` carries a module-level `from fastapi import ...` -- see the
# module docstring. `app.auth` (the package re-export) is deliberately NOT
# here: it is genuinely FastAPI-free, and
# tests/architecture/test_principal_transport_free.py is the accurate
# regression guard for that fact (a real subprocess import, not a name
# match) -- see the module docstring's rule 2-fastapi entry.
FASTAPI_CARRIERS = {"fastapi", "app.auth.jwt"}


@dataclass(frozen=True)
class Violation:
    rule: str
    importer: str
    imported: str
    lines: tuple[int, ...]

    def key(self) -> tuple[str, str, str]:
        return (self.rule, self.importer, self.imported)


def load_module_map(path: Path) -> dict[str, str]:
    return json.loads(path.read_text(encoding="utf-8"))["assignments"]


def load_baseline(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def assign_module(rel_posix: str, assignments: dict[str, str]) -> str | None:
    """Longest-prefix match: an exact file key wins over any directory key."""
    if rel_posix in assignments:
        return assignments[rel_posix]
    best: str | None = None
    best_len = -1
    for key, value in assignments.items():
        if key.endswith("/") and rel_posix.startswith(key) and len(key) > best_len:
            best, best_len = value, len(key)
    return best


def _iter_py_files(repo_root: Path, bases: tuple[str, ...] = ("app", "scripts")) -> list[Path]:
    out: list[Path] = []
    for base in bases:
        root = repo_root / base
        if root.exists():
            out.extend(sorted(root.rglob("*.py")))
    return out


def _imports_with_lines(source: str) -> list[tuple[str, int]]:
    """Every dotted module name a file imports, paired with its line number.
    Walks the whole tree (like the ADR 0035 template) so nested-function and
    ``if TYPE_CHECKING:``-guarded imports are caught too -- a signature that
    types a transport object is still a boundary leak even if the import
    guard means the module never loads FastAPI at runtime for a *consumer*
    of this file (this checker does not propagate through TYPE_CHECKING
    imports; see counter/rule 2-fastapi design notes in the report)."""
    tree = ast.parse(source)
    hits: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            hits.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            hits.append((node.module, node.lineno))
    return hits


def _module_to_file_candidates(modname: str) -> tuple[str, str]:
    stem = modname.replace(".", "/")
    return f"{stem}.py", f"{stem}/__init__.py"


def _is_control_plane(modname: str) -> bool:
    return any(
        modname == prefix or modname.startswith(prefix + ".") for prefix in CONTROL_PLANE_PREFIXES
    )


def find_violations(repo_root: Path, assignments: dict[str, str]) -> list[Violation]:
    files = _iter_py_files(repo_root)
    rel_files = [f.relative_to(repo_root).as_posix() for f in files]
    assign_of = {rel: assign_module(rel, assignments) for rel in rel_files}
    state_files = {rel for rel in rel_files if assign_of[rel] == "state"}
    ingress_files = {rel for rel in rel_files if assign_of[rel] == "ingress"}

    grouped: dict[tuple[str, str], dict[str, set]] = {}

    def record(rule: str, importer: str, name: str, line: int) -> None:
        bucket = grouped.setdefault((importer, rule), {"names": set(), "lines": set()})
        bucket["names"].add(name)
        bucket["lines"].add(line)

    for rel in rel_files:
        layer = assign_of[rel]
        if layer not in PRODUCTION_LAYERS:
            continue
        source = (repo_root / rel).read_text(encoding="utf-8")
        for modname, line in _imports_with_lines(source):
            file_candidate, init_candidate = _module_to_file_candidates(modname)
            resolves_to_state = file_candidate in state_files or init_candidate in state_files
            resolves_to_ingress = file_candidate in ingress_files or init_candidate in ingress_files

            if layer == "ingress" and resolves_to_state:
                record("1", rel, modname, line)

            if layer == "business":
                if modname in FASTAPI_CARRIERS or modname.startswith("fastapi."):
                    record("2-fastapi", rel, modname, line)
                if resolves_to_state:
                    record("2-state", rel, modname, line)
                if resolves_to_ingress:
                    record("3-ingress", rel, modname, line)

            if _is_control_plane(modname):
                record("7", rel, modname, line)

    violations = [
        Violation(
            rule=rule,
            importer=importer,
            imported=", ".join(sorted(bucket["names"])),
            lines=tuple(sorted(bucket["lines"])),
        )
        for (importer, rule), bucket in grouped.items()
    ]
    return sorted(violations, key=lambda v: (v.rule, v.importer))


def build_file_import_graph(repo_root: Path) -> dict[str, set[str]]:
    """Every app/ file to the app/ files it imports, resolved through the same
    candidate rules the layer checks use."""
    files = [f.relative_to(repo_root).as_posix() for f in _iter_py_files(repo_root, bases=("app",))]
    file_set = set(files)
    graph: dict[str, set[str]] = {rel: set() for rel in files}
    for rel in files:
        source = (repo_root / rel).read_text(encoding="utf-8")
        for modname, _line in _imports_with_lines(source):
            file_candidate, init_candidate = _module_to_file_candidates(modname)
            for candidate in (file_candidate, init_candidate):
                if candidate in file_set and candidate != rel:
                    graph[rel].add(candidate)
    return graph


def find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Iterative Tarjan strongly-connected components of size >= 2, each sorted."""
    all_nodes: set[str] = set(graph)
    for dests in graph.values():
        all_nodes.update(dests)
    index = 0
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    onstack: set[str] = set()
    stack: list[str] = []
    sccs: list[list[str]] = []
    for start in sorted(all_nodes):
        if start in indices:
            continue
        frame: list[tuple[str, Iterator[str], bool]] = [
            (start, iter(sorted(graph.get(start, ()))), False)
        ]
        while frame:
            node, neighbors, resumed = frame[-1]
            if not resumed:
                indices[node] = index
                lowlink[node] = index
                index += 1
                stack.append(node)
                onstack.add(node)
                frame[-1] = (node, neighbors, True)
            try:
                nxt = next(neighbors)
            except StopIteration:
                frame.pop()
                if lowlink[node] == indices[node]:
                    scc: list[str] = []
                    while True:
                        taken = stack.pop()
                        onstack.remove(taken)
                        scc.append(taken)
                        if taken == node:
                            break
                    if len(scc) >= 2:
                        sccs.append(sorted(scc))
                if frame:
                    parent, _, _ = frame[-1]
                    lowlink[parent] = min(lowlink[parent], lowlink[node])
                continue
            if nxt not in indices:
                frame.append((nxt, iter(sorted(graph.get(nxt, ()))), False))
            elif nxt in onstack:
                lowlink[node] = min(lowlink[node], indices[nxt])
    return sorted(sccs, key=lambda cycle: (cycle[0], len(cycle), cycle))


def load_cycles_baseline(path: Path) -> list[list[str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [list(cycle) for cycle in raw["cycles"]]


def compare_cycles(
    current: list[list[str]], baseline_entries: list[list[str]]
) -> tuple[list[list[str]], list[list[str]]]:
    current_keys = {tuple(cycle) for cycle in current}
    baseline_keys = {tuple(cycle) for cycle in baseline_entries}
    new = [list(key) for key in sorted(current_keys - baseline_keys)]
    stale = [list(key) for key in sorted(baseline_keys - current_keys)]
    return new, stale


def unmapped_app_files(repo_root: Path, assignments: dict[str, str]) -> list[str]:
    """Every ``app/**/*.py`` path `assign_module` cannot resolve to any layer.
    `scripts/` is excluded on purpose (best-effort only -- see module
    docstring); `app/` is the production surface this checker exists to
    protect, so a gap there must be loud, not a silent rule bypass."""
    out = []
    for f in _iter_py_files(repo_root, bases=("app",)):
        rel = f.relative_to(repo_root).as_posix()
        if assign_module(rel, assignments) is None:
            out.append(rel)
    return sorted(out)


def _rule_family(rule: str) -> str:
    """ "2-fastapi" / "2-state" both belong to plan section 5.5 rule "2"; this
    checker's baseline splits that rule's two clauses, module-map.json's own
    (human-authored) violations[] array does not -- family matching bridges
    the two schemas without requiring them to agree on sub-rule naming."""
    return rule.split("-", 1)[0]


def module_map_violations_without_enforcement_gap(
    module_map_path: Path, baseline_violations: list[dict]
) -> list[str]:
    """Every entry in module-map.json's own `violations[]` array must be
    traceable to this checker: either a matching exact-match baseline entry
    exists (by importer file + rule family -- string-formatting differences
    like `imported` ordering don't matter), or the map entry itself carries
    `"status": "documented-not-enforced"` (e.g. a file the map reclassified
    to a layer this checker structurally cannot flag, like
    app/rag/episodic.py -> control_plane). An entry that is neither is a
    silent gap between the human audit and the machine gate."""
    raw = json.loads(module_map_path.read_text(encoding="utf-8"))
    baseline_index = {(_rule_family(e["rule"]), e["importer"]) for e in baseline_violations}
    gaps = []
    for entry in raw.get("violations", []):
        if entry.get("status") == "documented-not-enforced":
            continue
        importer_file = entry["importer"].split(":")[0]
        key = (_rule_family(entry["rule"]), importer_file)
        if key not in baseline_index:
            gaps.append(f"[{entry['rule']}] {entry['importer']} -> {entry['imported']}")
    return gaps


def _settings_local_names(tree: ast.AST) -> set[str]:
    """Local names bound (with or without ``as``) to the literal symbol
    ``settings`` -- from `app.config` directly, or from any local
    re-exporting module (``from some.module import settings as cfg`` binds
    the local name ``cfg``, and reads through that alias must still count)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "settings":
                    names.add(alias.asname or alias.name)
    return names


def _config_module_local_paths(tree: ast.AST) -> set[tuple[str, ...]]:
    """Dotted-attribute *paths* (root name, then any further attribute hops)
    that reach the `app.config` MODULE object (not the `settings` symbol) --
    ``from app import config [as x]`` and ``import app.config [as x]`` both
    resolve to a single-name path (``("x",)`` / ``("config",)``); a BARE
    ``import app.config`` (no alias) binds only the top-level name ``app``,
    so real usage is a two-hop path: ``app.config.settings.<attr>`` ->
    ``("app", "config")``. Lets the counter catch module-qualified access
    (``config.settings.<attr>``) that never imports a name literally called
    ``settings`` at all."""
    paths: set[tuple[str, ...]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app":
            for alias in node.names:
                if alias.name == "config":
                    paths.add((alias.asname or alias.name,))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app.config":
                    paths.add((alias.asname,) if alias.asname else ("app", "config"))
    return paths


def _matches_path(node: ast.expr, path: tuple[str, ...]) -> bool:
    """Does `node` structurally read as ``path[0].path[1]. ... .path[-1]``?"""
    if len(path) == 1:
        return isinstance(node, ast.Name) and node.id == path[0]
    return (
        isinstance(node, ast.Attribute)
        and node.attr == path[-1]
        and _matches_path(node.value, path[:-1])
    )


def _count_settings_attribute_reads(
    tree: ast.AST, settings_names: set[str], config_module_paths: set[tuple[str, ...]]
) -> int:
    """Two attribute-read shapes, each counted once per AST node (they match
    disjoint node shapes, so a single expression can't double-count):
      - ``<settings_name>.<attr>`` where `settings_name` is bound directly to
        the `settings` symbol (aliased or not).
      - ``<config_module_path>.settings.<attr>`` -- module-qualified access
        that never binds a local name called `settings` at all; matching the
        OUTER attribute node only (not the inner `.settings` sub-expression,
        which is not itself an attribute *read off* settings)."""
    if not settings_names and not config_module_paths:
        return 0
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        base = node.value
        if isinstance(base, ast.Name) and base.id in settings_names:
            count += 1
        elif (
            isinstance(base, ast.Attribute)
            and base.attr == "settings"
            and any(_matches_path(base.value, path) for path in config_module_paths)
        ):
            count += 1
    return count


def find_settings_accessor_functions(repo_root: Path, assignments: dict[str, str]) -> list[str]:
    """Functions (in any non-composition-root file) whose body returns the
    bare `settings` symbol (``def get_settings(): return settings``). This
    evades attribute-level counting entirely -- callers read
    ``get_settings().attr``, never ``settings.attr`` -- so it cannot be fixed
    by extending the attribute-read count; it's a distinct, hard-fail
    pattern. Only a bare ``return <name>`` matches (``return settings.attr``
    is a normal, already-counted attribute read, not an accessor)."""
    hits: list[str] = []
    for rel in _rel_files(repo_root):
        if assign_module(rel, assignments) == "composition_root":
            continue
        tree = ast.parse((repo_root / rel).read_text(encoding="utf-8"))
        settings_names = _settings_local_names(tree)
        if not settings_names:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Return)
                    and isinstance(inner.value, ast.Name)
                    and inner.value.id in settings_names
                ):
                    hits.append(f"{rel}:{node.name}:{node.lineno}")
                    break
    return sorted(set(hits))


def count_settings_reads_outside_composition_root(
    repo_root: Path, assignments: dict[str, str]
) -> int:
    """AST attribute-access reads (``settings.<attr>``) in any non-composition-root
    file that binds a local name to the `settings` symbol -- directly, via an
    alias (``as cfg``), or via a module-qualified chain
    (``config.settings.<attr>``). A module that re-exports the name still
    counts as a direct reader for each of its own importers, because each
    importer independently binds a local name via its own import statement
    and is scanned the same way, regardless of the name it chose."""
    total = 0
    for rel in _rel_files(repo_root):
        if assign_module(rel, assignments) == "composition_root":
            continue
        tree = ast.parse((repo_root / rel).read_text(encoding="utf-8"))
        settings_names = _settings_local_names(tree)
        config_names = _config_module_local_paths(tree)
        total += _count_settings_attribute_reads(tree, settings_names, config_names)
    return total


def count_ingress_services_symbols(repo_root: Path, assignments: dict[str, str]) -> int:
    """Distinct (module, symbol) pairs imported from `app.services*` by any
    ingress-assigned file -- the ingress layer's actual import surface onto the
    business layer today, tracked as a ratchet ahead of a formal narrow
    Ingress-to-Business interface (a later workstream's deliverable)."""
    symbols: set[tuple[str, str]] = set()
    for rel in _rel_files(repo_root):
        if assign_module(rel, assignments) != "ingress":
            continue
        tree = ast.parse((repo_root / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and (node.module == "app.services" or node.module.startswith("app.services."))
            ):
                symbols.update((node.module, alias.name) for alias in node.names)
    return len(symbols)


def _rel_files(repo_root: Path) -> list[str]:
    return [f.relative_to(repo_root).as_posix() for f in _iter_py_files(repo_root)]


def compare_violations(
    current: list[Violation], baseline_entries: list[dict]
) -> tuple[list[Violation], list[dict]]:
    baseline_keys = {(e["rule"], e["importer"], e["imported"]) for e in baseline_entries}
    current_keys = {v.key() for v in current}
    new = [v for v in current if v.key() not in baseline_keys]
    stale = [
        e for e in baseline_entries if (e["rule"], e["importer"], e["imported"]) not in current_keys
    ]
    return new, stale


def check_counters(
    repo_root: Path, assignments: dict[str, str], counters_baseline: dict
) -> list[str]:
    failures: list[str] = []

    accessors = find_settings_accessor_functions(repo_root, assignments)
    if accessors:
        failures.append(
            "settings accessor function(s) found -- these evade attribute-level "
            "counting entirely (callers read some_fn().attr, never settings.attr "
            "directly); rename to read settings.<attr> at the call site, or make "
            "the accessor itself the reviewed seam: " + "; ".join(accessors)
        )

    settings_count = count_settings_reads_outside_composition_root(repo_root, assignments)
    settings_limit = counters_baseline["settings_reads_outside_composition_root"]["count"]
    if settings_count > settings_limit:
        failures.append(
            "settings.<attribute> reads outside the composition root grew: "
            f"{settings_count} > baseline {settings_limit}"
        )

    services_count = count_ingress_services_symbols(repo_root, assignments)
    services_limit = counters_baseline["ingress_services_symbols"]["count"]
    if services_count > services_limit:
        failures.append(
            "distinct app/services symbols imported by ingress modules grew: "
            f"{services_count} > baseline {services_limit}"
        )

    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--module-map", type=Path, default=DEFAULT_MODULE_MAP)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--cycles-baseline", type=Path, default=None)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--show-counts",
        action="store_true",
        help="Print the two live counter values and exit 0 (does not gate).",
    )
    parser.add_argument(
        "--show-cycles",
        action="store_true",
        help="Print file-level strongly-connected components and exit 0.",
    )
    args = parser.parse_args(argv)

    assignments = load_module_map(args.module_map)

    if args.show_counts:
        print(
            "settings_reads_outside_composition_root="
            f"{count_settings_reads_outside_composition_root(args.repo_root, assignments)}"
        )
        services_count = count_ingress_services_symbols(args.repo_root, assignments)
        print(f"ingress_services_symbols={services_count}")
        return 0

    if args.show_cycles:
        cycles = find_cycles(build_file_import_graph(args.repo_root))
        json.dump(cycles, sys.stdout, indent=2)
        print()
        return 0

    baseline = load_baseline(args.baseline)
    unmapped = unmapped_app_files(args.repo_root, assignments)
    map_gaps = module_map_violations_without_enforcement_gap(
        args.module_map, baseline["violations"]
    )
    current = find_violations(args.repo_root, assignments)
    new, stale = compare_violations(current, baseline["violations"])
    counter_failures = check_counters(args.repo_root, assignments, baseline["counters"])
    live_cycles = find_cycles(build_file_import_graph(args.repo_root))
    cycles_path = args.cycles_baseline or (
        args.repo_root / "docs/superpowers/import-cycles-baseline.json"
    )
    baseline_cycles = load_cycles_baseline(cycles_path) if cycles_path.exists() else []
    new_cycles, stale_cycles = compare_cycles(live_cycles, baseline_cycles)

    ok = True
    if unmapped:
        ok = False
        print(
            "UNMAPPED APP MODULES (module-map.json assigns no layer -- every "
            "rule above silently skips these; add an assignment entry):",
            file=sys.stderr,
        )
        for rel in unmapped:
            print(f"  {rel}", file=sys.stderr)
    if map_gaps:
        ok = False
        print(
            "MODULE MAP / BASELINE INCONSISTENCY (module-map.json's own "
            "violations[] lists an entry this checker cannot account for -- "
            'add a matching baseline entry, or mark it "status": '
            '"documented-not-enforced" with a reason):',
            file=sys.stderr,
        )
        for gap in map_gaps:
            print(f"  {gap}", file=sys.stderr)
    if new:
        ok = False
        print(
            "NEW VIOLATIONS (not in the baseline -- new architectural debt; "
            "fix it, or get owner sign-off and add a baseline entry with owner+expires):",
            file=sys.stderr,
        )
        for v in new:
            lines = ",".join(str(n) for n in v.lines)
            print(f"  [{v.rule}] {v.importer}:{lines} -> {v.imported}", file=sys.stderr)
    if stale:
        ok = False
        print(
            "STALE BASELINE ENTRIES (violation no longer found in code -- "
            "remove the entry in the same change that fixed it):",
            file=sys.stderr,
        )
        for e in stale:
            print(f"  [{e['rule']}] {e['importer']} -> {e['imported']}", file=sys.stderr)
    if counter_failures:
        ok = False
        print("COUNTER REGRESSIONS:", file=sys.stderr)
        for message in counter_failures:
            print(f"  {message}", file=sys.stderr)
    if new_cycles:
        ok = False
        print(
            "NEW IMPORT CYCLES (not in import-cycles-baseline.json -- "
            "break the cycle, or get owner sign-off and add a baseline entry):",
            file=sys.stderr,
        )
        for cycle in new_cycles:
            print("  " + " -> ".join(cycle), file=sys.stderr)
    if stale_cycles:
        ok = False
        print(
            "STALE CYCLE BASELINE ENTRIES (cycle no longer found -- "
            "remove the entry in the same change that broke it):",
            file=sys.stderr,
        )
        for cycle in stale_cycles:
            print("  " + " -> ".join(cycle), file=sys.stderr)

    if ok:
        print(
            f"OK: {len(current)} violation(s) match the baseline exactly; "
            "counters within their recorded limits."
        )
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
