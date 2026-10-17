"""Detail attribute definitions and alias resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.business_query.plan.filter_tree import AttributePredicate, FilterGroup
from app.business_query.plan.query_plan import BusinessQueryPlan

if TYPE_CHECKING:
    from app.business_query.definitions import DefinitionBundle, DetailDefinition


def _normalize_alias(raw: str) -> str:
    return " ".join(raw.strip().lower().split())


def _normalize_family_alias(raw: str) -> str:
    """Normalize family labels without doing fuzzy or substring matching."""
    return " ".join(
        raw.strip().casefold().replace(".", " ").replace("_", " ").replace("-", " ").split()
    )


def _signed_family_aliases(detail: Any) -> set[str]:
    """Return only aliases that the signed definition can justify.

    ``DetailDefinition.aliases`` is used for value labels on enum families.
    Such labels must not become family aliases.  A non-enum family with no
    value mapping can safely use its signed labels as family aliases.
    """
    aliases = {detail.family_key, detail.logical_source}
    owner_prefix = f"{detail.owner_resource}."
    if detail.logical_source.startswith(owner_prefix):
        aliases.add(detail.logical_source[len(owner_prefix) :])
    aliases.update(detail.family_aliases)
    if not detail.value_mapping:
        aliases.update(detail.aliases)
    return {alias for alias in aliases if isinstance(alias, str) and alias.strip()}


def _signed_family_alias_map(bundle: DefinitionBundle) -> dict[str, frozenset[str]]:
    aliases: dict[str, set[str]] = {}
    for detail in bundle.detail_definitions:
        for alias in _signed_family_aliases(detail):
            aliases.setdefault(_normalize_family_alias(alias), set()).add(detail.family_key)
    return {alias: frozenset(families) for alias, families in aliases.items()}


def normalize_detail_family(family: str, *, bundle: DefinitionBundle | None = None) -> str | None:
    """Resolve one planner family label to one unique signed family key.

    The resolver accepts canonical keys and exact normalized aliases from the
    signed bundle. Unknown or ambiguous labels return ``None``. In particular,
    it never picks a merely related family as a substitute.
    """
    if bundle is None:
        return None
    exact = {
        detail.family_key for detail in bundle.detail_definitions if detail.family_key == family
    }
    if len(exact) == 1:
        return family
    candidates = _signed_family_alias_map(bundle).get(_normalize_family_alias(family), frozenset())
    return next(iter(candidates)) if len(candidates) == 1 else None


def canonicalize_detail_families(
    plan: BusinessQueryPlan, *, bundle: DefinitionBundle
) -> BusinessQueryPlan:
    """Canonicalize detail selection and predicate family keys from the bundle.

    Unresolved labels remain unchanged so the shared visibility check reports
    the normal ``member_not_found`` outcome instead of silently substituting a
    different family.
    """

    def canonical_family(family: str) -> str:
        return normalize_detail_family(family, bundle=bundle) or family

    def canonicalize_group(group: FilterGroup | None) -> FilterGroup | None:
        if group is None:
            return None

        def canonicalize_node(node: object) -> object:
            if isinstance(node, AttributePredicate):
                return node.model_copy(update={"family_key": canonical_family(node.family_key)})
            if isinstance(node, FilterGroup):
                return canonicalize_group(node)
            return node

        return group.model_copy(
            update={
                "all": [canonicalize_node(node) for node in group.all],
                "any": [canonicalize_node(node) for node in group.any],
            }
        )

    selections = [
        selection.model_copy(update={"family": canonical_family(selection.family)})
        for selection in plan.detail_selections
    ]
    predicates = [
        predicate.model_copy(update={"family_key": canonical_family(predicate.family_key)})
        for predicate in plan.attribute_predicates
    ]
    return plan.model_copy(
        update={
            "detail_selections": selections,
            "attribute_predicates": predicates,
            "filters": canonicalize_group(plan.filters),
            "having": canonicalize_group(plan.having),
        }
    )


class FamilyAliasIndex:
    """One shared index over a bundle's detail definitions and their aliases.

    Every family-lookup helper below reads this index instead of scanning
    ``bundle.detail_definitions`` and rebuilding its own ad hoc dict:

    - ``definitions_for`` / ``family_keys`` give the "all definitions for one
      family_key" / "every known family_key" lookups a single owner.
    - ``resolve_selection_family`` is the alias dict
      ``canonicalize_detail_selections`` reads to canonicalize an
      already-selected family string.
    - ``families_mentioned_in`` is the alias-phrase table
      ``align_detail_selections_to_explicit_question`` reads to scan free
      question text.

    The two alias lookups intentionally keep their own normalization: one
    canonicalizes an already-selected family string (loose, prefix/token
    fallback included), the other scans free question text for a signed
    phrase (exact word-boundary match only). They are different jobs, so
    they get different lookup tables built from the same source data.
    """

    def __init__(self, definitions: list[DetailDefinition]) -> None:
        self._by_family_key: dict[str, list[DetailDefinition]] = {}
        self._selection_alias_to_family: dict[str, str] = {}
        self._question_phrases: list[tuple[str, str]] = []

        for definition in definitions:
            self._by_family_key.setdefault(definition.family_key, []).append(definition)
            self._index_selection_alias(definition)
            self._index_question_phrase(definition)

    def definitions_for(self, family_key: str) -> list[DetailDefinition]:
        """All signed revisions for one family key, in bundle order."""
        return self._by_family_key.get(family_key, [])

    def family_keys(self) -> list[str]:
        """Every known family key, sorted."""
        return sorted(self._by_family_key)

    def _index_selection_alias(self, definition: DetailDefinition) -> None:
        family = definition.family_key
        self._selection_alias_to_family[family.casefold()] = family
        self._selection_alias_to_family[family] = family
        prefix = family.split(".")[0].casefold()
        self._selection_alias_to_family.setdefault(prefix, family)
        for alias in definition.family_aliases:
            self._selection_alias_to_family[_normalize_alias(alias)] = family
            self._selection_alias_to_family[alias.casefold()] = family
            self._selection_alias_to_family[alias] = family
            for token in alias.split():
                self._selection_alias_to_family.setdefault(token.casefold(), family)
        for alias in definition.aliases:
            self._selection_alias_to_family[_normalize_alias(alias)] = family
            self._selection_alias_to_family[alias.casefold()] = family
            self._selection_alias_to_family[alias] = family

    def resolve_selection_family(self, family: str) -> str:
        """Canonicalize a ``detail_selections.family`` string.

        Returns the input unchanged when nothing signed matches, so the
        shared visibility check reports the normal refusal instead of a
        silent substitution.
        """
        return (
            self._selection_alias_to_family.get(family)
            or self._selection_alias_to_family.get(family.casefold())
            or self._selection_alias_to_family.get(_normalize_alias(family))
            or family
        )

    def _index_question_phrase(self, definition: DetailDefinition) -> None:
        for alias in definition.family_aliases:
            phrase = _normalize_family_alias(alias)
            if phrase:
                self._question_phrases.append((phrase, definition.family_key))

    def families_mentioned_in(self, question: str) -> set[str]:
        """Family keys whose signed alias phrase appears as a whole word in ``question``."""
        padded_question = f" {_normalize_family_alias(question)} "
        return {
            family for phrase, family in self._question_phrases if f" {phrase} " in padded_question
        }


def resolve_detail_definition(
    family_key: str,
    revision_hash: str | None = None,
    *,
    bundle: DefinitionBundle | None = None,
) -> DetailDefinition | None:
    """Resolve a family from the selected signed bundle only."""
    if bundle is None:
        return None
    candidates = FamilyAliasIndex(bundle.detail_definitions).definitions_for(family_key)
    matches = [
        signed
        for signed in candidates
        if revision_hash is None or signed.revision_hash == revision_hash
    ]
    if not matches:
        return None
    # DefinitionBundle validation guarantees one current revision for a
    # multi-revision family. A predicate without a revision therefore follows
    # the signed current definition; an explicit revision remains pinned to
    # the immutable historical definition.
    return (
        matches[0]
        if revision_hash is not None or len(matches) == 1
        else next((candidate for candidate in matches if candidate.is_current), None)
    )


def resolve_alias_to_value(
    family_key: str,
    raw_alias: str,
    *,
    bundle: DefinitionBundle | None = None,
    revision_hash: str | None = None,
) -> tuple[Any, DetailDefinition] | None:
    """Resolve a user/model alias string to a typed value and signed definition."""
    norm = _normalize_alias(raw_alias)
    if bundle is None:
        return None

    matches: list[tuple[Any, DetailDefinition]] = []
    for signed in FamilyAliasIndex(bundle.detail_definitions).definitions_for(family_key):
        if revision_hash is not None and signed.revision_hash != revision_hash:
            continue
        definition = resolve_detail_definition(family_key, signed.revision_hash, bundle=bundle)
        if definition is None:
            continue
        mapping = _definition_value_mapping(definition)
        if norm in mapping:
            matches.append((mapping[norm], definition))
    if not matches:
        return None
    # The same alias may exist in two revisions only when it resolves to the
    # same typed value.  Different meanings are ambiguous and fail closed.
    values = {repr(value) for value, _definition in matches}
    if len(values) != 1:
        return None
    return matches[0]


def get_known_detail_definitions(
    *, bundle: DefinitionBundle | None = None
) -> list[DetailDefinition]:
    """List current signed detail definitions from a bundle."""
    if bundle is None:
        return []
    index = FamilyAliasIndex(bundle.detail_definitions)
    definitions: list[DetailDefinition] = []
    for family_key in index.family_keys():
        definition = resolve_detail_definition(family_key, bundle=bundle)
        if definition is not None:
            definitions.append(definition)
    return definitions


def _definition_value_mapping(definition: DetailDefinition) -> dict[str, Any]:
    """Normalize signed aliases and canonical values for one revision."""
    mapping = {_normalize_alias(alias): value for alias, value in definition.value_mapping.items()}
    # A Billing export may list aliases separately from the value mapping. It
    # is safe to use a separately declared alias only when the mapping also
    # provides its target; otherwise the alias is intentionally unresolved.
    for value in list(mapping.values()):
        mapping.setdefault(_normalize_alias(str(value)), value)
    return mapping


def canonicalize_attribute_predicates(
    plan: BusinessQueryPlan, *, bundle: DefinitionBundle
) -> BusinessQueryPlan:
    """Map user-facing detail aliases to the signed canonical values.

    The planner card intentionally shows both aliases (for example ``1 side
    ptg``) and canonical values (``one_side``).  The compiler compares the
    stored typed value, so accepting the alias at the public plan seam must be
    deterministic and bundle-owned rather than relying on model behavior.

    Raises:
        PlanRefused: an ``eq``/``neq``/``in``/``not_in`` predicate names an
            alias the signed bundle cannot resolve to exactly one value. This
            check runs before any predicate value is built: for ``neq`` and
            ``not_in`` specifically, a raw unresolved string reaching SQL
            would make the predicate spuriously true for every real row
            (it is "not equal" to everything real) instead of refusing the
            plan, so the refusal has to happen here, not downstream.
    """
    if not plan.attribute_predicates and plan.filters is None and plan.having is None:
        return plan

    from app.business_query.outcomes import PlanRefused

    index = FamilyAliasIndex(bundle.detail_definitions)

    def canonicalize_one(predicate: AttributePredicate) -> AttributePredicate:
        signed_definitions = index.definitions_for(predicate.family_key)
        if not signed_definitions or predicate.operator not in {"eq", "neq", "in", "not_in"}:
            return predicate

        candidate_definitions = [
            detail
            for detail in signed_definitions
            if predicate.revision_hash is None or detail.revision_hash == predicate.revision_hash
        ]
        if not candidate_definitions:
            return predicate
        values: list[str | int | float | bool] = []
        resolved_revisions: set[str] = set()
        for value in predicate.values:
            if not isinstance(value, str):
                values.append(value)
                continue
            normalized = _normalize_alias(value)
            matches: list[tuple[Any, Any]] = []
            for signed in candidate_definitions:
                definition = resolve_detail_definition(
                    predicate.family_key, signed.revision_hash, bundle=bundle
                )
                if definition is None:
                    continue
                mapping = _definition_value_mapping(definition)
                if normalized in mapping:
                    matches.append((mapping[normalized], signed))
            distinct_values = {repr(item[0]) for item in matches}
            if not matches or len(distinct_values) > 1:
                raise PlanRefused("member_not_found")
            typed_value, signed = matches[0]
            values.append(typed_value)
            resolved_revisions.add(signed.revision_hash)

        update: dict[str, Any] = {"values": values}
        if (
            predicate.revision_hash is None
            and len(candidate_definitions) > 1
            and len(resolved_revisions) == 1
        ):
            revision = next(iter(resolved_revisions))
            update.update({"revision_hash": revision, "definition_revision": revision})
        return predicate.model_copy(update=update)

    def canonicalize_group(group: FilterGroup | None) -> FilterGroup | None:
        if group is None:
            return None

        def canonicalize_node(node: object) -> object:
            if isinstance(node, AttributePredicate):
                return canonicalize_one(node)
            if isinstance(node, FilterGroup):
                return canonicalize_group(node)
            return node

        return group.model_copy(
            update={
                "all": [canonicalize_node(node) for node in group.all],
                "any": [canonicalize_node(node) for node in group.any],
            }
        )

    predicates = [canonicalize_one(predicate) for predicate in plan.attribute_predicates]

    return plan.model_copy(
        update={
            "attribute_predicates": predicates,
            "filters": canonicalize_group(plan.filters),
            "having": canonicalize_group(plan.having),
        }
    )


def canonicalize_detail_selections(
    plan: BusinessQueryPlan, *, bundle: DefinitionBundle
) -> BusinessQueryPlan:
    """Map signed detail family aliases in detail_selections to canonical family_key."""
    if not plan.detail_selections:
        return plan
    index = FamilyAliasIndex(bundle.detail_definitions)
    new_selections = [
        selection.model_copy(update={"family": index.resolve_selection_family(selection.family)})
        for selection in plan.detail_selections
    ]
    return plan.model_copy(update={"detail_selections": new_selections})
