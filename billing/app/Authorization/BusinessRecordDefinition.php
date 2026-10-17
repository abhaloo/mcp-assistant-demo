<?php

declare(strict_types=1);

namespace App\Authorization;

/**
 * Immutable description of one Global Search / AI-authorized resource type.
 *
 * One instance per entry in App\Search\GlobalSearchService::registerDefaults().
 * This is data only — no ambient reads, no DB access. AiAccessPolicy is the
 * pure code that turns a definition + an explicit User/permission list into
 * an authorization decision.
 */
final readonly class BusinessRecordDefinition
{
    /** Compare the record's entity_id to the user's entity_id; Super Admin bypasses. */
    public const string ENTITY_SCOPE_STANDARD = 'standard';

    /** Standard, plus: when the user has a department_id, the record's department_id must match too
     *  (Super Admin bypasses the entity check but NOT the department check — WorkOrderVisibilityScope). */
    public const string ENTITY_SCOPE_DEPARTMENT = 'department';

    /** The model has no entity_id of its own; scoping is via a parent relation's entity_id,
     *  which the caller must resolve and pass in the record array (e.g. CreditNote -> bill). */
    public const string ENTITY_SCOPE_VIA_PARENT = 'via_parent';

    public const string PERMISSION_MODE_ANY = 'any';

    public const string PERMISSION_MODE_ALL = 'all';

    public const string DEPARTMENT_SCOPE_NONE = 'none';

    public const string DEPARTMENT_SCOPE_FILTER_WHEN_PRESENT = 'filter_when_present';

    public const string DEPARTMENT_SCOPE_REQUIRED_MATCH = 'required_match';

    /**
     * @param  string  $resourceType  Stable Global Search group key (e.g. 'invoice').
     * @param  string  $modelClass  Eloquent model class backing this resource type.
     * @param  string  $searchPermission  Permission required for the resource to appear in search.
     * @param  list<string>  $readPermissions  Permission(s) required to view/open one record.
     * @param  string  $readPermissionMode  'any' (OR, default) or 'all' (AND) across $readPermissions.
     * @param  list<string>|null  $linkPermissions  Permission(s) required to render a navigable
     *                                              link to the record. Null means "same as $readPermissions" — most resource types do
     *                                              not distinguish "can view" from "can link" today; Product is the one exception
     *                                              (its history link additionally requires 'view inventory').
     * @param  string  $linkPermissionMode  'any' or 'all' across the effective link permissions.
     * @param  string  $entityScope  One of the ENTITY_SCOPE_* constants.
     * @param  string|null  $parentRelation  Relation name used to resolve entity scope when
     *                                       $entityScope is ENTITY_SCOPE_VIA_PARENT (e.g. 'bill' for CreditNote).
     * @param  array<string,string>  $recordPredicates  Column => required value a record must
     *                                                  match to actually BE this resource type (e.g. Bill.type = 'Invoice'). Empty when
     *                                                  the model class is not shared across multiple resource types.
     * @param  list<string>  $fieldSets  Named field-set catalogue for this resource type (Task
     *                                   B3 / R1-R2) — shared, unmodified, between AiAccessPolicy::snapshot()'s
     *                                   per-resource `field_sets` and PolicyManifestExporter's manifest entry, so
     *                                   the two can never drift apart. 'summary'/'detail' is the minimum every
     *                                   record type declares today; a future field-set split (e.g. a narrower
     *                                   'link' set) would widen this list, not replace it.
     * @param  array<string,list<string>>  $fieldSetFields  Task B6 / R1 — the exact, ordered field
     *                                                      list PER field-set name (keys MUST match $fieldSets, same order).
     *                                                      PolicyManifestExporter derives the manifest's `field_sets` list from
     *                                                      these keys and validates every member is in PolicyManifestExporter's
     *                                                      `readable_fields` for this resource type (fails the export otherwise —
     *                                                      see PolicyManifestValidator). Where a resource has only one effective
     *                                                      set today, every declared set is the SAME full field list — this task
     *                                                      encodes what exists, it does not invent a narrower field-visibility
     *                                                      policy (see task-B6-brief.md's Core design).
     * @param  ?string  $scopeEntityColumn  Task B6 / R1 — the projection column (ai_v1_<type>) RAG's
     *                                      executor binds the principal's entity_id against. Always
     *                                      'entity_id' today, INCLUDING via_parent resources like credit_note
     *                                      (whose view projects the PARENT's entity_id under that same column
     *                                      name — see ProjectionDefinitions::creditNoteView()). Null is permitted
     *                                      ONLY when $scopeEntityColumnNullJustified is true (none expected today
     *                                      — every one of the ten registry resources has a bindable entity column).
     * @param  ?string  $scopeDepartmentColumn  Task B6 / R1 — the projection column RAG's executor binds
     *                                          the principal's department_id against, or null when this resource
     *                                          type carries no department scope (nine of the ten resources; only
     *                                          'job' declares 'department_id').
     * @param  bool  $scopeEntityColumnNullJustified  Task B6 / R1 — the registry-level justification flag
     *                                                PolicyManifestValidator requires before it will accept
     *                                                $scopeEntityColumn === null. Defaults false; no resource sets this
     *                                                true today.
     * @param  ?string  $departmentScopeMode  Task B1b / F4 (2026-07-27 adversarial review) — UNDECLARED
     *                                        (null) by default, never a silent permissive value. A resource that
     *                                        declares $scopeDepartmentColumn MUST also declare an explicit mode
     *                                        here (PolicyManifestValidator::assertDepartmentScopeModeIsDeclaredWhenColumnPresent()
     *                                        fails the export otherwise) — 'none' + a declared column is still a
     *                                        legal, explicit state (plan §3.2); only the silent DEFAULT is forbidden.
     *                                        A resource with NO department column may leave this null; the exporter
     *                                        resolves that to 'none' itself (there is nothing to scope).
     * @param  list<string>  $operations  Structured record operations this resource permits.
     * @param  array<string, array{type: string, operators: list<string>}>  $typedFilters  Task B1b / R1 —
     *                                                                                     additional, explicitly-typed filter fields beyond filterFieldNames()'s
     *                                                                                     base set (id/entity_id/department_id/predicates), each declaring its own
     *                                                                                     allowed operator list. Legacy filters stay `{field, type}` only (an absent
     *                                                                                     `operators` key means `['eq']` to the consumer) — an entry here always
     *                                                                                     emits an explicit list. Empty for every resource except job (`created_at`
     *                                                                                     -> datetime, eq/gt/gte/lt/lte — ADR 0030's Minimum Structured Recovery).
     * @param  array<string, list<string>>  $sortDirectionOverrides  Task B1b / R1 — per-sort-field allowed
     *                                                               direction override, keyed by one of PolicyManifestExporter::SORT_FIELDS.
     *                                                               Absent == `['asc']` to the consumer. Empty for every resource except job
     *                                                               (`created_at` -> `['asc', 'desc']`, ADR 0030).
     * @param  list<string>  $groupableFields  Task B1b / R1 — fields RAG's group_count/top_groups operations may
     *                                         group by; PolicyManifestValidator requires every member to already be
     *                                         in readableFields() (mirrors RAG's own groupable_fields <= readable_fields
     *                                         rule). Empty for every resource today: ADR 0030 approves job's status/
     *                                         priority/department_name as the only group dimensions, but none of the
     *                                         three is a column the CURRENT `ai_v1_job` projection view SELECTs yet
     *                                         (Projections\ProjectionDefinitions::jobView()) — adding them here without
     *                                         that view change first would make export() itself throw (an unmapped
     *                                         projection column) for every resource, not just job. That view change is
     *                                         explicitly out of this task's scope (task-B1b-brief.md's hard
     *                                         constraint: "do not touch projection DDL — that is B2"). See
     *                                         PolicyManifestExporter::export()'s own docblock for the full trace.
     */
    public function __construct(
        public string $resourceType,
        public string $modelClass,
        public string $searchPermission,
        public array $readPermissions,
        public string $readPermissionMode = self::PERMISSION_MODE_ANY,
        public ?array $linkPermissions = null,
        public string $linkPermissionMode = self::PERMISSION_MODE_ANY,
        public string $entityScope = self::ENTITY_SCOPE_STANDARD,
        public ?string $parentRelation = null,
        public array $recordPredicates = [],
        public array $fieldSets = ['summary', 'detail'],
        public array $fieldSetFields = [],
        public ?string $scopeEntityColumn = 'entity_id',
        public ?string $scopeDepartmentColumn = null,
        public bool $scopeEntityColumnNullJustified = false,
        public ?string $departmentScopeMode = null,
        public array $operations = ['search', 'list', 'get'],
        public array $typedFilters = [],
        public array $sortDirectionOverrides = [],
        public array $groupableFields = [],
    ) {}

    /** @return list<string> */
    public function effectiveLinkPermissions(): array
    {
        return $this->linkPermissions ?? $this->readPermissions;
    }

    public function effectiveLinkPermissionMode(): string
    {
        return $this->linkPermissions === null ? $this->readPermissionMode : $this->linkPermissionMode;
    }

    /**
     * Task P3 — the stable, de-duplicated union of every $fieldSetFields
     * entry: the canonical source PolicyManifestExporter/RecordContextResolver
     * both read directly instead of maintaining a second, independently
     * declared allowlist (PolicyManifestExporter::READABLE_FIELDS, deleted).
     * Pure: no IO, derived only from $this->fieldSetFields. Order is first
     * occurrence across $fieldSetFields' own iteration order (which itself
     * matches $fieldSets' declared order, per
     * BusinessRecordRegistryTest::every_definitions_field_set_fields_keys_match_its_field_sets_in_order) —
     * a field repeated in a later set does not move or duplicate.
     *
     * @return list<string>
     */
    public function readableFields(): array
    {
        $fields = [];
        foreach ($this->fieldSetFields as $setFields) {
            foreach ($setFields as $field) {
                $fields[$field] = true;
            }
        }

        return array_keys($fields);
    }

    /**
     * Task P3 — the manifest FILTER field names this definition contributes
     * (relocated from PolicyManifestExporter's private HAS_OWN_ENTITY_ID
     * const + filtersFor(), Task 1C): always 'id'; 'entity_id' unless this
     * resource is scoped ENTITY_SCOPE_VIA_PARENT (a via_parent resource has
     * no entity_id column of its own on its backing table to filter on
     * directly — see BusinessRecordRegistry's credit_note definition, whose
     * ai_v1_credit_note view PROJECTS a joined entity_id under
     * $scopeEntityColumn for authorization binding, a distinct concern from
     * this ad hoc filter list); 'department_id' for
     * ENTITY_SCOPE_DEPARTMENT; then every declared record predicate column,
     * in declaration order. Pure: derived only from $this->entityScope/
     * $this->recordPredicates, matching HAS_OWN_ENTITY_ID's membership
     * exactly for all ten registry resources today (via_parent is the ONLY
     * reason a resource type lacks its own entity_id column).
     *
     * @return list<string>
     */
    public function filterFieldNames(): array
    {
        $fields = ['id'];

        if ($this->entityScope !== self::ENTITY_SCOPE_VIA_PARENT) {
            $fields[] = 'entity_id';
        }

        if ($this->entityScope === self::ENTITY_SCOPE_DEPARTMENT) {
            $fields[] = 'department_id';
        }

        return [...$fields, ...array_keys($this->recordPredicates)];
    }
}
