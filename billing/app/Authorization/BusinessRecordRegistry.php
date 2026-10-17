<?php

declare(strict_types=1);

namespace App\Authorization;

use App\Models\Bill;
use App\Models\CreditNote;
use App\Models\Customer;
use App\Models\CustomerOrder;
use App\Models\Inventory;
use App\Models\Product;
use App\Models\Supplier;
use App\Models\WorkOrder;
use Illuminate\Database\Eloquent\Model;
use InvalidArgumentException;

/**
 * Stable registry of BusinessRecordDefinitions for all ten Global Search
 * resource types (App\Search\GlobalSearchService::registerDefaults()).
 *
 * This is the canonical inventory Task B1 (R1) requires: one definition per
 * resource type, capturing the permission(s), entity-scoping rule, and any
 * department/subtype/parent visibility rule the UI currently enforces via
 * scopes and policies (WorkOrderVisibilityScope, EntityTrait, BillPolicy,
 * CustomerPolicy, CustomerOrderPolicy, and the ProductInventoryHistory link
 * gate).
 *
 * Task B1b / F5: deliberately NOT final — PolicyManifestExporterTest injects
 * a test-double subclass overriding get() (splicing in one deliberately-bad
 * definition for a single resource type while every other resource's
 * definition still comes from the real, hardcoded defaults()) to prove
 * export() itself calls each PolicyManifestValidator guard, not just that
 * the validator is correct in isolation. Mirrors the same "deliberately not
 * final" discipline Projections\ProjectionDefinitions already documents for
 * its own test double. No other production code depends on this class being
 * un-subclassable.
 */
class BusinessRecordRegistry
{
    /** @var array<string, BusinessRecordDefinition> */
    private array $definitions;

    public function __construct()
    {
        $this->definitions = self::defaults();
    }

    public function get(string $resourceType): BusinessRecordDefinition
    {
        return $this->definitions[$resourceType]
            ?? throw new InvalidArgumentException("Unknown business record resource type [{$resourceType}].");
    }

    public function has(string $resourceType): bool
    {
        return isset($this->definitions[$resourceType]);
    }

    /** @return list<string> */
    public function resourceTypes(): array
    {
        return array_keys($this->definitions);
    }

    /** @return array<string, BusinessRecordDefinition> */
    public function all(): array
    {
        return $this->definitions;
    }

    /**
     * Task P3 — resolves a loaded model instance to its resource type,
     * matching model class PLUS declared record predicates: three of the ten
     * resource types (invoice/quotation/payable_quotation) share Bill as
     * their $modelClass, so class alone is not enough to tell a Bill row's
     * resource type apart — the same record predicates AiAccessPolicy
     * already checks (matchesRecordPredicates()) decide it here too. A model
     * whose class matches no definition, or whose class matches but whose
     * predicate columns don't, returns null (this later supports
     * `bill.show` — a definitive per-record resource type, not just "some
     * Bill").
     *
     * Pure: no IO of its own — reads only $model's already-loaded
     * attributes (the same `$model->{$column}` access
     * RecordContextResolver::scopingAttributes() already uses), never
     * queries or loads relations itself.
     */
    public function resourceTypeForModel(Model $model): ?string
    {
        foreach ($this->definitions as $resourceType => $definition) {
            if (! $model instanceof $definition->modelClass) {
                continue;
            }

            if ($this->matchesRecordPredicates($definition, $model)) {
                return $resourceType;
            }
        }

        return null;
    }

    private function matchesRecordPredicates(BusinessRecordDefinition $definition, Model $model): bool
    {
        foreach ($definition->recordPredicates as $column => $expected) {
            if (($model->{$column} ?? null) !== $expected) {
                return false;
            }
        }

        return true;
    }

    /** @return array<string, BusinessRecordDefinition> */
    private static function defaults(): array
    {
        return [
            'invoice' => new BusinessRecordDefinition(
                resourceType: 'invoice',
                modelClass: Bill::class,
                searchPermission: 'view invoice',
                readPermissions: ['view invoice', 'view quotation'],
                recordPredicates: ['type' => 'Invoice'],
                fieldSetFields: self::billFieldSetFields(),
            ),
            'quotation' => new BusinessRecordDefinition(
                resourceType: 'quotation',
                modelClass: Bill::class,
                searchPermission: 'view quotation',
                readPermissions: ['view invoice', 'view quotation'],
                recordPredicates: ['type' => 'Quotation'],
                fieldSetFields: self::billFieldSetFields(),
            ),
            // Payable Quotation entitlement correction (Task B1 / R3): search,
            // read, and link all use the single new 'view payable quotation'
            // permission, and the canonical fail-closed predicate requires
            // BOTH the discriminator columns to agree.
            'payable_quotation' => new BusinessRecordDefinition(
                resourceType: 'payable_quotation',
                modelClass: Bill::class,
                searchPermission: 'view payable quotation',
                readPermissions: ['view payable quotation'],
                recordPredicates: ['type' => 'Payable Quotation', 'payable' => 'Yes'],
                fieldSetFields: self::billFieldSetFields(),
            ),
            // CreditNote has no entity_id of its own; Global Search's
            // 'credit_note' scopeCallback filters via whereHas('bill.entity'),
            // and clicking a result opens the underlying Bill through
            // BillPolicy (same permission set as invoice/quotation).
            //
            // Task B6 / R1: scopeEntityColumn stays the default 'entity_id' —
            // ai_v1_credit_note projects the PARENT bill's entity_id under
            // that same column name (ProjectionDefinitions::creditNoteView()),
            // which is exactly the durable fix the A5 cross-repo
            // reconciliation note calls for (RAG's executor can now bind a
            // predicate on this resource instead of failing closed).
            'credit_note' => new BusinessRecordDefinition(
                resourceType: 'credit_note',
                modelClass: CreditNote::class,
                searchPermission: 'view invoice',
                readPermissions: ['view invoice', 'view quotation'],
                entityScope: BusinessRecordDefinition::ENTITY_SCOPE_VIA_PARENT,
                parentRelation: 'bill',
                fieldSetFields: self::sameFieldsForEverySet(['id', 'description', 'bill.invoice_number']),
            ),
            'customer_order' => new BusinessRecordDefinition(
                resourceType: 'customer_order',
                modelClass: CustomerOrder::class,
                searchPermission: 'view customer order',
                readPermissions: ['view customer order'],
                fieldSetFields: self::sameFieldsForEverySet(['id', 'order_number', 'description', 'customer.name']),
            ),
            // WorkOrderVisibilityScope: Super Admin bypasses entity isolation
            // but NOT an assigned department (app/Models/Scopes/WorkOrderVisibilityScope.php ~30-44).
            // Task B6 / R1: the one resource type that declares a
            // scopeDepartmentColumn — ai_v1_job projects department_id
            // (ProjectionDefinitions::jobView()).
            //
            // Task B2 / §3.6, ADR 0030 (Minimum Structured Recovery) — the ABSORBED
            // completion of task-B1b's deferred requirement 3/5 (ledger blocker
            // B1b-groupable-fields-blocked-on-b2-projection): `status`/`priority`
            // (plain work_orders passthrough columns) and `department.name` (joined
            // from departments.name) are now readable — safe only because
            // ProjectionDefinitions::jobView() SELECTs all three as
            // of the immediately-prior slice (task-internal ordering,
            // task-B2-brief.md: "projection columns land BEFORE the registry
            // declarations that reference them"). `department_id` REMAINS a hidden
            // scope column only — never readable, never groupable (plan §3.6).
            // group_count/top_groups land back in `operations` in this SAME slice as
            // the non-empty `groupableFields`, satisfying
            // PolicyManifestValidator::assertGroupOperationsHaveGroupableFields() —
            // this supersedes the B1b fix-round-1 narrowing (see git history on this
            // entry for that interim state).
            'job' => new BusinessRecordDefinition(
                resourceType: 'job',
                modelClass: WorkOrder::class,
                searchPermission: 'view job',
                readPermissions: ['view job'],
                entityScope: BusinessRecordDefinition::ENTITY_SCOPE_DEPARTMENT,
                fieldSetFields: self::sameFieldsForEverySet([
                    'id', 'work_number', 'title', 'description', 'customer.name',
                    'status', 'priority', 'department.name',
                ]),
                scopeDepartmentColumn: 'department_id',
                departmentScopeMode: BusinessRecordDefinition::DEPARTMENT_SCOPE_FILTER_WHEN_PRESENT,
                operations: ['search', 'list', 'get', 'count', 'group_count', 'top_groups'],
                // ADR 0030 (Minimum Structured Recovery): created_at (B1b, range operators,
                // already a projected ai_v1_job column via PolicyManifestExporter::SORT_FIELDS)
                // plus status/priority (B2, equality only — a job is either IN PROGRESS or it
                // isn't; there is no "greater than IN PROGRESS"). Both are real ai_v1_job
                // columns as of the immediately-prior slice, satisfying
                // PolicyManifestValidator::assertTypedFiltersAreProjected().
                typedFilters: [
                    'created_at' => ['type' => 'datetime', 'operators' => ['eq', 'gt', 'gte', 'lt', 'lte']],
                    'status' => ['type' => 'string', 'operators' => ['eq']],
                    'priority' => ['type' => 'string', 'operators' => ['eq']],
                ],
                sortDirectionOverrides: ['created_at' => ['asc', 'desc']],
                // ADR 0030's approved operational dimensions plus customer_name,
                // which is already readable and is required by the confirmed S5
                // bounded top-customers acceptance case. department_id remains
                // hidden and cannot be grouped.
                groupableFields: ['status', 'priority', 'department_name', 'customer_name'],
            ),
            'customer' => new BusinessRecordDefinition(
                resourceType: 'customer',
                modelClass: Customer::class,
                searchPermission: 'view customer',
                readPermissions: ['view customer'],
                fieldSetFields: self::sameFieldsForEverySet(['id', 'name', 'business_name', 'phone', 'email']),
            ),
            'supplier' => new BusinessRecordDefinition(
                resourceType: 'supplier',
                modelClass: Supplier::class,
                searchPermission: 'view supplier',
                readPermissions: ['view supplier'],
                fieldSetFields: self::sameFieldsForEverySet(['id', 'name', 'phone', 'email']),
            ),
            // GlobalSearchService's product urlBuilder only renders a link when
            // the user also has 'view inventory' (app/Search/GlobalSearchService.php ~285-287).
            'product' => new BusinessRecordDefinition(
                resourceType: 'product',
                modelClass: Product::class,
                searchPermission: 'view product',
                readPermissions: ['view product'],
                linkPermissions: ['view product', 'view inventory'],
                linkPermissionMode: BusinessRecordDefinition::PERMISSION_MODE_ALL,
                fieldSetFields: self::sameFieldsForEverySet(['id', 'name', 'sku', 'category.name']),
            ),
            'inventory' => new BusinessRecordDefinition(
                resourceType: 'inventory',
                modelClass: Inventory::class,
                searchPermission: 'view inventory',
                readPermissions: ['view inventory'],
                fieldSetFields: self::sameFieldsForEverySet(
                    ['id', 'reference', 'description', 'supplier_invoice_no', 'supplier.name']
                ),
            ),
        ];
    }

    /**
     * Task B6 / R1 — the shared field_set_fields for the three Bill-subtype
     * resources (invoice/quotation/payable_quotation). Task P3: this is now
     * the ONLY declaration of these fields — PolicyManifestExporter's
     * former, independently-maintained READABLE_FIELDS allowlist was deleted;
     * every consumer reads BusinessRecordDefinition::readableFields()
     * (derived from this method's return value) instead.
     *
     * @return array<string,list<string>>
     */
    private static function billFieldSetFields(): array
    {
        return self::sameFieldsForEverySet(['id', 'invoice_number', 'description', 'customer.name', 'payment_status']);
    }

    /**
     * Task B6 / R1 — no resource type in this registry encodes a narrower
     * "summary" field list anywhere today (not in GlobalSearchService's
     * fields/with arrays, not in any prior B3/B4 task): every field_sets
     * name this registry declares ('summary', 'detail') maps to the SAME
     * full readable_fields list. This mirrors task-B6-brief.md's own
     * fallback rule verbatim ("Where a resource today has only one
     * effective set, summary == full list and detail ... is the same") —
     * it is not a guess, it is the only reading the existing code supports.
     * $fields is what BusinessRecordDefinition::readableFields() derives
     * (Task P3) and PolicyManifestExporterTest pins exactly.
     *
     * @param  list<string>  $fields
     * @return array<string,list<string>>
     */
    private static function sameFieldsForEverySet(array $fields): array
    {
        return [
            'summary' => $fields,
            'detail' => $fields,
        ];
    }
}
