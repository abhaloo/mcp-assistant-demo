<?php

declare(strict_types=1);

namespace App\Authorization;

use App\Authorization\BusinessQuery\BusinessDefinitionRegistry;
use App\Models\Entity;
use App\Models\User;

/**
 * The one canonical AI-record-authorization decision point (Task B1 / R2).
 *
 * Construct with a FRESHLY LOADED User plus an EXPLICIT permission-name list
 * (never read via $user->can() or auth() inside this class) and it computes:
 *
 *  - capability sets per resource type (search / read / link);
 *  - explicit query scoping (entity/department/subtype predicates), as data;
 *  - reference authorization ("may this user see resource type X, record Y?");
 *  - link authorization;
 *  - (flag-gated) Financial Analytics grants for record_access schema v3.
 *
 * No ambient auth() reads, no reliance on Eloquent model boot order, no
 * static/global mutable state — every instance is independent and immutable.
 * The DB touches are `$user->hasRole('Super Admin')` and
 * `$user->primaryRoleName()` (which reads `$user->roles`), both lazy
 * relationship queries scoped to the explicitly-passed $user (the same
 * pattern already used by WorkOrderPolicy/CustomerPolicy/CustomerOrderPolicy)
 * — not ambient state.
 *
 * `snapshot()` builds the `record_access` JWT claim body: v2 by default
 * (`services.rag.snapshot_v3` OFF). v3 emission adds explicit Financial
 * Analytics grants + definition_bundle_hash only when that flag is ON —
 * RAG's consumer is still Literal[2] today, so default OFF is required.
 */
final readonly class AiAccessPolicy
{
    /**
     * @param  list<string>  $permissions  Explicit permission names — the caller resolves these
     *                                     (e.g. from a freshly-loaded $user->getAllPermissions() or a signed claim list)
     *                                     and passes them in; this class never calls $user->can() itself.
     */
    private function __construct(
        private int $userEntityId,
        private ?int $userDepartmentId,
        private bool $isSuperAdmin,
        private string $primaryRole,
        private array $permissions,
        private BusinessRecordRegistry $registry,
        private BusinessDefinitionRegistry $definitions,
    ) {}

    /**
     * @param  list<string>  $permissions
     */
    public static function forUser(
        User $user,
        array $permissions,
        ?BusinessRecordRegistry $registry = null,
        ?BusinessDefinitionRegistry $definitions = null,
    ): self {
        return new self(
            userEntityId: (int) $user->entity_id,
            userDepartmentId: $user->department_id !== null ? (int) $user->department_id : null,
            isSuperAdmin: $user->hasRole('Super Admin'),
            primaryRole: $user->primaryRoleName(),
            permissions: $permissions,
            registry: $registry ?? new BusinessRecordRegistry,
            definitions: $definitions ?? BusinessDefinitionRegistry::default(),
        );
    }

    /**
     * @return array{search: bool, read: bool, link: bool}
     */
    public function capabilities(string $resourceType): array
    {
        $definition = $this->registry->get($resourceType);

        $read = $this->satisfies($definition->readPermissions, $definition->readPermissionMode);

        return [
            'search' => $this->hasPermission($definition->searchPermission),
            'read' => $read,
            'link' => $this->satisfies(
                $definition->effectiveLinkPermissions(),
                $definition->effectiveLinkPermissionMode(),
            ),
        ];
    }

    /**
     * Reference authorization: may this user see this resource type/record?
     *
     * @param  array<string,mixed>  $record  Plain scoping attributes for the candidate record:
     *                                       'entity_id' (required — for via-parent-scoped resources the caller resolves the
     *                                       parent's entity_id first), optional 'department_id', and any columns named in the
     *                                       definition's $recordPredicates (e.g. 'type', 'payable' for Bill subtypes).
     */
    public function canView(string $resourceType, array $record): bool
    {
        $definition = $this->registry->get($resourceType);

        return $this->satisfies($definition->readPermissions, $definition->readPermissionMode)
            && $this->matchesRecordPredicates($definition, $record)
            && $this->withinScope($definition, $record);
    }

    /**
     * Link authorization. Identical rule to canView() for every resource type
     * today except Product (see BusinessRecordDefinition::$linkPermissions) —
     * kept as a distinct method so callers have a stable seam if that changes.
     *
     * @param  array<string,mixed>  $record
     */
    public function canLink(string $resourceType, array $record): bool
    {
        $definition = $this->registry->get($resourceType);

        return $this->satisfies($definition->effectiveLinkPermissions(), $definition->effectiveLinkPermissionMode())
            && $this->matchesRecordPredicates($definition, $record)
            && $this->withinScope($definition, $record);
    }

    /**
     * Explicit query scoping for one resource type: the entity/department/
     * subtype predicates a caller must apply. Plain data, no closures.
     */
    public function scopeFor(string $resourceType): QueryScope
    {
        $definition = $this->registry->get($resourceType);

        $departmentId = $definition->entityScope === BusinessRecordDefinition::ENTITY_SCOPE_DEPARTMENT
            ? $this->userDepartmentId
            : null;

        return new QueryScope(
            entityId: $this->userEntityId,
            crossEntity: $this->isSuperAdmin,
            departmentId: $departmentId,
            recordPredicates: $definition->recordPredicates,
        );
    }

    /**
     * Build the v2 `record_access` JWT claim body (Task B3 / R1) — the
     * binding shape from the auditable plan §6.2, RagClient's job to attach
     * verbatim as the `record_access` claim.
     *
     * Only `resources` entries the user has AT LEAST ONE action for are
     * included — a resource type with zero granted actions is omitted
     * entirely rather than emitted with an empty `actions` list, keeping the
     * claim proportional to what this user can actually touch.
     *
     * Wire-shape note (Critical review fix, Task B3): `resources` is a JSON
     * OBJECT on the wire (RAG validates it as `dict[str, ResourceGrant]`,
     * strict=True). A PHP empty array `[]` and an empty map are the same
     * value in PHP but `json_encode([])` produces `[]` (a JSON ARRAY), which
     * RAG's strict validator rejects outright — silently 401ing every user
     * with zero granted resource actions (a bare user, or plausibly a
     * finance-only user whose document_tiers come from bill/payment
     * permissions with no resource search/read permissions). When $resources
     * ends up empty we return `new \stdClass()` instead of `[]` so it
     * `json_encode`s to `{}`, never `[]`. This is scoped to the one field
     * that is empty here — PolicyManifestExporter's JSON_FORCE_OBJECT-style
     * per-field handling exists for the same reason (see that class) rather
     * than forcing every array to an object globally, which would corrupt
     * the list-valued fields (`document_tiers`, `actions`, `field_sets`).
     *
     * @param  string  $manifestHash  sha256:-prefixed hex digest of the canonical
     *                                policy manifest (see PolicyManifestExporter::hash()) — an EXPLICIT input,
     *                                not computed here, so this class never depends on the manifest's own
     *                                construction.
     * @param  string|null  $definitionBundleHash  Required (non-empty sha256:… prefix) when snapshot_v3 is on;
     *                                             ignored while the flag is off. Missing/empty throws InvalidArgumentException.
     * @return array{
     *     schema_version: int,
     *     manifest_hash: string,
     *     entity_id: int,
     *     cross_entity: bool,
     *     document_tiers: list<string>,
     *     scope_values: array{department_id: int|null},
     *     resources: array<string, array{actions: list<string>, field_sets: list<string>}>|\stdClass,
     *     definition_bundle_hash?: string,
     *     financial_analytics?: array{capabilities: list<string>},
     * }
     *
     * @throws \InvalidArgumentException when snapshot_v3 is on and definition_bundle_hash is missing/empty/unprefixed
     */
    public function snapshot(string $manifestHash, ?string $definitionBundleHash = null): array
    {
        $resources = [];

        foreach ($this->registry->resourceTypes() as $resourceType) {
            $capabilities = $this->capabilities($resourceType);

            $actions = array_values(array_filter(
                ['search', 'read', 'link'],
                fn (string $action): bool => $capabilities[$action],
            ));

            if ($actions === []) {
                continue;
            }

            $resources[$resourceType] = [
                'actions' => $actions,
                'field_sets' => $this->registry->get($resourceType)->fieldSets,
            ];
        }

        $snapshot = [
            'schema_version' => 2,
            'manifest_hash' => $manifestHash,
            'entity_id' => $this->userEntityId,
            'cross_entity' => $this->isSuperAdmin,
            'document_tiers' => DocumentTierMapper::tiersFor($this->primaryRole, $this->permissions),
            'scope_values' => [
                'department_id' => $this->userDepartmentId,
            ],
            // json_encode([]) === '[]', a JSON array — RAG's strict dict[str, ResourceGrant]
            // validator rejects that outright. Force the empty case to encode as '{}'.
            'resources' => $resources === [] ? new \stdClass : $resources,
        ];

        // The account spend limit rides only behind the deploy-order flag: RAG's
        // RecordAccess is strict and forbids unknown keys, so this claim is
        // emitted only once the RAG reader that declares it is live (consumer first).
        $askBudget = $this->entityAskBudget();
        if ($askBudget !== null) {
            $snapshot['ask_budget'] = $askBudget;
        }

        if (! config('services.rag.snapshot_v3', false)) {
            return $snapshot;
        }

        if ($definitionBundleHash === null || $definitionBundleHash === ''
            || ! str_starts_with($definitionBundleHash, 'sha256:')) {
            throw new \InvalidArgumentException(
                'definition_bundle_hash is required when services.rag.snapshot_v3 is enabled (sha256:… prefixed).',
            );
        }

        $snapshot['schema_version'] = 3;
        $snapshot['definition_bundle_hash'] = $definitionBundleHash;
        $snapshot['financial_analytics'] = [
            'capabilities' => $this->financialAnalyticsGrants(),
        ];

        return $snapshot;
    }

    /**
     * The account's Ask AI spend limit, ready for the record_access claim.
     * Null unless the claim flag is on and the entity holds a limit — the key
     * is omitted entirely otherwise, like the resource-omission rule above.
     *
     * @return array{limit_usd: string, period: string}|null
     */
    private function entityAskBudget(): ?array
    {
        if (! config('services.rag.ask_budget_claim', false)) {
            return null;
        }

        $limit = Entity::query()->whereKey($this->userEntityId)->value('ask_budget_usd');

        if ($limit === null) {
            return null;
        }

        return [
            'limit_usd' => (string) number_format((float) $limit, 2, '.', ''),
            'period' => 'month',
        ];
    }

    /**
     * Explicit Financial Analytics capability names granted to this principal.
     * Empty while snapshot_v3 is off. Empty required_permissions never grant;
     * decision_pending / unsupported entries never grant.
     *
     * @return list<string>
     */
    public function financialAnalyticsGrants(): array
    {
        if (! config('services.rag.snapshot_v3', false)) {
            return [];
        }

        $granted = [];
        foreach ($this->definitions->definitions()['capabilities'] as $entry) {
            if ($entry->capabilityState !== 'enabled') {
                continue;
            }
            if (! $this->satisfies($entry->requiredPermissions, BusinessRecordDefinition::PERMISSION_MODE_ALL)) {
                continue;
            }
            $granted[] = $entry->name;
        }

        return $granted;
    }

    /**
     * @param  array<string,mixed>  $record
     */
    private function matchesRecordPredicates(BusinessRecordDefinition $definition, array $record): bool
    {
        foreach ($definition->recordPredicates as $column => $expected) {
            if (($record[$column] ?? null) !== $expected) {
                return false;
            }
        }

        return true;
    }

    /**
     * @param  array<string,mixed>  $record
     */
    private function withinScope(BusinessRecordDefinition $definition, array $record): bool
    {
        $recordEntityId = isset($record['entity_id']) ? (int) $record['entity_id'] : null;

        if (! $this->isSuperAdmin && $recordEntityId !== $this->userEntityId) {
            return false;
        }

        if ($definition->entityScope === BusinessRecordDefinition::ENTITY_SCOPE_DEPARTMENT
            && $this->userDepartmentId !== null) {
            $recordDepartmentId = isset($record['department_id']) ? (int) $record['department_id'] : null;

            if ($recordDepartmentId !== $this->userDepartmentId) {
                return false;
            }
        }

        return true;
    }

    /**
     * Fail-closed: an empty permission list is never satisfied. A resource
     * type MUST declare at least one required permission (enforced by
     * BusinessRecordRegistryTest's completeness check) — this is a second,
     * independent guard so a future empty $readPermissions/$linkPermissions
     * cannot silently become "everyone is authorized" here.
     *
     * @param  list<string>  $permissions
     */
    private function satisfies(array $permissions, string $mode): bool
    {
        if ($permissions === []) {
            return false;
        }

        if ($mode === BusinessRecordDefinition::PERMISSION_MODE_ALL) {
            foreach ($permissions as $permission) {
                if (! $this->hasPermission($permission)) {
                    return false;
                }
            }

            return true;
        }

        foreach ($permissions as $permission) {
            if ($this->hasPermission($permission)) {
                return true;
            }
        }

        return false;
    }

    private function hasPermission(string $permission): bool
    {
        return in_array($permission, $this->permissions, true);
    }
}
