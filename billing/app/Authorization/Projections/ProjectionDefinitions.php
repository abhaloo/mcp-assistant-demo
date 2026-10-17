<?php

declare(strict_types=1);

namespace App\Authorization\Projections;

use App\Authorization\BusinessRecordRegistry;
use App\Authorization\PolicyManifestExporter;
use InvalidArgumentException;

/**
 * Task B5 / R1 — one `ai_v1_*` view definition per BusinessRecordRegistry
 * projection, enumerated FROM the registry (never a hand-copied list): if a
 * resource #11 is ever registered without a corresponding view builder below,
 * build()'s match falls through to its throwing default arm and `all()`
 * fails loudly instead of silently omitting a view.
 *
 * Each view SELECTs exactly the resource's manifest `readable_fields`
 * (dotted relation fields like `customer.name` become a LEFT JOINed column,
 * aliased `customer_name` — see outputColumnFor()) plus its
 * ProjectionScopeColumns — no `SELECT *`, no extra columns. Subtype
 * predicates (Bill.type / Bill.payable) are baked into the view's WHERE
 * clause so a credential that can only see views still cannot cross a
 * subtype boundary; entity/department scoping is NOT baked in (the view
 * only carries the columns — binding the predicate is RAG's executor's job).
 *
 * Task B6.1 / F1: deliberately NOT `final` — PolicyManifestExporterTest
 * injects a test-double subclass overriding `all()` (via
 * PolicyManifestExporter's optional `$projections` constructor param) to
 * prove `export()` genuinely calls PolicyManifestValidator rather than
 * only ever seeing real, clean registry data. No other production code
 * depends on this class being un-subclassable.
 */
class ProjectionDefinitions
{
    /**
     * Task P3 — NO exporter instance held here anymore. This class used to
     * construct `new PolicyManifestExporter($this->registry)` in its own
     * constructor while PolicyManifestExporter::export() constructed `new
     * ProjectionDefinitions($this->registry)` right back when none was
     * injected — an exporter/projection construction cycle. Readable-field
     * names now come straight from the registry
     * (BusinessRecordDefinition::readableFields()) and filter-field names
     * from the same definition (BusinessRecordDefinition::filterFieldNames())
     * — both pure, registry-side data. Only the sort-field NAMES still come
     * from PolicyManifestExporter, via ITS static sortFields() accessor (a
     * stateless call, not an object construction) — see that method's own
     * docblock for why the constant's ownership stays there.
     */
    /**
     * Task B2 / §3.7 — `$driver` decides two things per view this class
     * builds: (1) whether `payment_status`/`department_name` get the
     * canonical `CAST(... AS CHAR CHARACTER SET utf8mb4) COLLATE
     * utf8mb4_unicode_ci` expression (collatedText()) or a plain
     * `CAST(... AS TEXT)` that merely COMPILES on SQLite (verified
     * empirically: SQLite's CAST grammar rejects `CHARACTER SET`, and a
     * literal `COLLATE utf8mb4_unicode_ci` clause makes EVERY query against
     * the view fail with "no such collation sequence" — even a plain
     * `SELECT id FROM view` untouched by the collated column — so the
     * degraded SQLite form is not just cosmetic, it is what keeps every
     * other projection test able to run at all); (2) each view's
     * `atomicReplace` flag (ProjectionViewDefinition::applyStatements()) —
     * MySQL supports one atomic `CREATE OR REPLACE VIEW`, SQLite does not
     * (a syntax error there, verified empirically) and stays on the legacy
     * drop-then-create pair.
     *
     * Defaults to the literal string 'mysql' — NOT a `DB::connection()`
     * facade call — deliberately: this keeps the class instantiable with
     * zero framework bootstrap (ProjectionDefinitionsTest extends plain
     * PHPUnit\Framework\TestCase, no Laravel container) and keeps
     * PolicyManifestExporter::export()'s "no DB, no ambient reads"
     * determinism guarantee intact (export() only ever reads `.columns`,
     * which is identical regardless of driver — only `.selectSql` varies).
     * 'mysql' matches both config/database.php's own default connection and
     * the production target; ApplyAiProjectionViews builds its OWN
     * driver-aware instance from the connection it is actually applying to
     * (see that class), never relying on this default for a real apply.
     */
    public function __construct(
        private readonly BusinessRecordRegistry $registry = new BusinessRecordRegistry,
        private readonly string $driver = 'mysql',
    ) {}

    /** @return array<string, ProjectionViewDefinition> keyed by resourceType, one per registry projection. */
    public function all(): array
    {
        $definitions = [];
        foreach ($this->registry->resourceTypes() as $resourceType) {
            $definitions[$resourceType] = $this->build($resourceType);
        }

        return $definitions;
    }

    /** Dotted relation fields flatten to `relation_column` (e.g. `customer.name` -> `customer_name`). */
    public static function outputColumnFor(string $readableField): string
    {
        return str_replace('.', '_', $readableField);
    }

    /**
     * Task B2 / §3.7 — the canonical collation treatment for a DERIVED text
     * expression (a CASE expression, a joined-table column) — never applied
     * to a plain single-table passthrough column, which inherits its source
     * column's collation implicitly. MySQL gets the exact expression
     * task-B2-brief.md / plan §3.7 specify; SQLite gets a plain
     * `CAST(... AS TEXT)` — confirmed by empirical test to compile AND stay
     * queryable (this class's own constructor docblock explains why the
     * real utf8mb4_unicode_ci clause cannot be used verbatim there). This is
     * NOT collation evidence on SQLite — see ApplyAiProjectionViews' --check
     * extension and this task's ledger proof-limit note for where the real
     * MySQL proof lands.
     */
    private function collatedText(string $expr): string
    {
        return $this->driver === 'sqlite'
            ? "CAST(({$expr}) AS TEXT)"
            : "CAST(({$expr}) AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci";
    }

    /**
     * Task B2 / §3.7 — MySQL supports one atomic `CREATE OR REPLACE VIEW`
     * (no drop-before-create gap for a concurrent reader); SQLite does not
     * (verified empirically: a syntax error, not a semantic failure) and
     * must keep using the legacy drop-then-create pair.
     */
    private function atomicReplaceSupported(): bool
    {
        return $this->driver !== 'sqlite';
    }

    private function build(string $resourceType): ProjectionViewDefinition
    {
        return match ($resourceType) {
            'invoice' => $this->billView($resourceType, "b.type = 'Invoice'"),
            'quotation' => $this->billView($resourceType, "b.type = 'Quotation'"),
            'payable_quotation' => $this->billView($resourceType, "b.type = 'Payable Quotation' AND b.payable = 'Yes'"),
            'credit_note' => $this->creditNoteView($resourceType),
            'customer_order' => $this->customerOrderView($resourceType),
            'job' => $this->jobView($resourceType),
            'customer' => $this->customerView($resourceType),
            'supplier' => $this->supplierView($resourceType),
            'product' => $this->productView($resourceType),
            'inventory' => $this->inventoryView($resourceType),
            default => throw new InvalidArgumentException(
                "No ai_v1_* projection view definition for resource type [{$resourceType}]. "
                .'Add a builder branch in '.self::class.'::build() (Task B5 / R1).'
            ),
        };
    }

    /**
     * Qualifying-payment account types for Bill::payments() (app/Models/Bill.php)
     * — Journal rows on one of these account types, with debit > 0. Duplicated
     * here (not imported from Account) deliberately: this class stays a pure,
     * framework-light SQL generator with no Eloquent/model dependency, the same
     * "independently inventoried" discipline BusinessRecordRegistry's own
     * class docblock documents for its resource definitions.
     */
    private const QUALIFYING_ACCOUNT_TYPES = ['OPERATING_EXPENSE', 'PAYABLE', 'CASH', 'BANK'];

    /**
     * The three Bill-subtype views (invoice / quotation / payable_quotation)
     * share one shape: id, invoice_number, description, customer.name, entity_id
     * + the subtype discriminator column(s) baked into $predicateSql, plus the
     * canonical `payment_status` derived field (Task B5 / R2) matching
     * App\Support\BillPaymentState::compute()/classify() exactly.
     *
     * items_total and payments_total are each a SEPARATE correlated scalar
     * subquery (never a JOIN to bill_items/journals) — the same "never joined"
     * invariant BillPaymentState's own docblock documents, so a bill with N
     * items and M qualifying payments never becomes an N*M row fan-out. Both
     * are computed in an inner derived table (aliased bt) so payment_status's
     * CASE expression can reference them by name once instead of repeating
     * either subquery three times.
     *
     * Every arithmetic operand is CAST(...AS DOUBLE) — both MySQL 8 and SQLite
     * resolve DOUBLE to an 8-byte IEEE-754 float, the same representation
     * PHP's native float uses, and the item-value expression is grouped
     * EXACTLY as BillPaymentState::itemsTotal() computes it
     * (`$itemValue + ($itemValue * ($tax / 100))`, not the algebraically
     * equivalent but not bit-identical `$itemValue * (1 + $tax / 100)`) so the
     * `balance == 0` PAID boundary lands on the same value PHP's loop
     * produces, not just a close approximation.
     *
     * CAVEAT (Task B5 review F1 / B6 R0 rider — documentation only, no
     * semantic change here): this view's `payment_status` matches
     * `BillPaymentState`'s and `Bill::payments()`'s DECLARED rule (the
     * qualifying account types + `debit > 0`, replicated exactly above) —
     * but it does NOT match runtime `Bill::paymentState()` for a non-admin
     * viewer. `App\Models\Journal::boot()` (app/Models/Journal.php:54-66)
     * registers an ambient global scope `where('cash', false)` whenever
     * `auth()->user()->role !== 'admin'`, which excludes cash-account
     * journal rows from `Bill::payments()` for that viewer at runtime. This
     * view has no such role-conditional scope (it is viewer-INDEPENDENT by
     * construction — one canonical value per bill, not one per viewer), so a
     * bill with a qualifying cash receipt can show PAID/PARTIAL PAID here
     * while a non-admin viewer's UI badge shows PARTIAL PAID/UNPAID. Whether
     * canonical `payment_status` should stay viewer-independent (this view,
     * as built) or be made to match the role-scoped UI is a product decision
     * escalated to the user — NOT resolved in this task. No consumer may
     * wire to `payment_status` until that decision is made.
     */
    private function billView(string $resourceType, string $predicateSql): ProjectionViewDefinition
    {
        $viewName = ProjectionViewName::forResource($resourceType);
        $columns = $this->columnsFor($resourceType);

        $itemValue = '(CAST(bi.price AS DOUBLE) * CAST(bi.quantity AS DOUBLE) - CAST(bi.discount AS DOUBLE))';
        $itemsTotalSubquery = "COALESCE((SELECT SUM({$itemValue} + ({$itemValue} * (CAST(bi.tax AS DOUBLE) / 100))) "
            .'FROM bill_items bi WHERE bi.bill_id = b.id), 0)';

        $qualifyingTypes = implode(', ', array_map(
            fn (string $type) => "'{$type}'",
            self::QUALIFYING_ACCOUNT_TYPES,
        ));
        $paymentsTotalSubquery = 'COALESCE((SELECT SUM(CAST(j.debit AS DOUBLE)) FROM journals j '
            .'INNER JOIN accounts a ON a.id = j.account_id '
            ."WHERE j.bill_id = b.id AND j.debit > 0 AND a.account_type IN ({$qualifyingTypes})), 0)";

        $innerSql = 'SELECT b.id, b.invoice_number, b.description, b.customer_id, b.entity_id, b.type, b.payable, '
            .'b.created_at, b.updated_at, '
            ."{$itemsTotalSubquery} AS items_total, {$paymentsTotalSubquery} AS payments_total "
            ."FROM bills b WHERE {$predicateSql}";

        $paymentStatusCase = 'CASE '
            .'WHEN (bt.items_total - bt.payments_total) = 0 THEN \'PAID\' '
            .'WHEN (bt.items_total - bt.payments_total) < 0 THEN \'OVERPAID\' '
            .'WHEN (bt.items_total - bt.payments_total) < bt.items_total THEN \'PARTIAL PAID\' '
            .'ELSE \'UNPAID\' END';

        $selectList = $this->selectListSql($columns, [
            'id' => 'bt.id',
            'invoice_number' => 'bt.invoice_number',
            'description' => 'bt.description',
            'customer_name' => 'c.name',
            'entity_id' => 'bt.entity_id',
            'type' => 'bt.type',
            'payable' => 'bt.payable',
            'created_at' => 'bt.created_at',
            'updated_at' => 'bt.updated_at',
            // Task B2 / §3.7 — payment_status is a CASE expression, not a
            // plain passthrough column: its output charset/collation is
            // otherwise engine-determined rather than tied to a real source
            // column, so it gets the explicit canonical collation cast.
            'payment_status' => $this->collatedText($paymentStatusCase),
        ]);

        $sql = "SELECT {$selectList} FROM ({$innerSql}) bt LEFT JOIN customers c ON c.id = bt.customer_id";

        return new ProjectionViewDefinition(
            $resourceType,
            $viewName,
            $columns,
            $sql,
            derivedTextColumns: array_values(array_intersect($columns, ['payment_status'])),
            atomicReplace: $this->atomicReplaceSupported(),
        );
    }

    private function creditNoteView(string $resourceType): ProjectionViewDefinition
    {
        $viewName = ProjectionViewName::forResource($resourceType);
        $columns = $this->columnsFor($resourceType);
        $selectList = $this->selectListSql($columns, [
            'id' => 'cn.id',
            'description' => 'cn.description',
            'bill_invoice_number' => 'pb.invoice_number',
            'entity_id' => 'pb.entity_id',
            'bill_id' => 'cn.bill_id',
            'created_at' => 'cn.created_at',
            'updated_at' => 'cn.updated_at',
        ]);

        $sql = "SELECT {$selectList} FROM credit_notes cn LEFT JOIN bills pb ON pb.id = cn.bill_id";

        return new ProjectionViewDefinition($resourceType, $viewName, $columns, $sql, atomicReplace: $this->atomicReplaceSupported());
    }

    private function customerOrderView(string $resourceType): ProjectionViewDefinition
    {
        $viewName = ProjectionViewName::forResource($resourceType);
        $columns = $this->columnsFor($resourceType);
        $selectList = $this->selectListSql($columns, [
            'id' => 'co.id',
            'order_number' => 'co.order_number',
            'description' => 'co.description',
            'customer_name' => 'c.name',
            'entity_id' => 'co.entity_id',
            'created_at' => 'co.created_at',
            'updated_at' => 'co.updated_at',
        ]);

        $sql = "SELECT {$selectList} FROM customer_orders co LEFT JOIN customers c ON c.id = co.customer_id";

        return new ProjectionViewDefinition($resourceType, $viewName, $columns, $sql, atomicReplace: $this->atomicReplaceSupported());
    }

    /**
     * Task B2 / §3.6 — additive fields (department_id already existed as a
     * hidden scope column; it stays exactly that — never readable, never
     * groupable): `status`/`priority` are plain work_orders passthrough
     * columns (no collation treatment — see collatedText()'s own docblock:
     * only a CASE expression or a joined-table column counts as "derived").
     * `department_name` IS a joined-table column (departments.name via a
     * LEFT JOIN on work_orders.department_id, matching plan §3.6 exactly)
     * and gets the canonical collation cast.
     */
    private function jobView(string $resourceType): ProjectionViewDefinition
    {
        $viewName = ProjectionViewName::forResource($resourceType);
        $columns = $this->columnsFor($resourceType);
        $selectList = $this->selectListSql($columns, [
            'id' => 'wo.id',
            'work_number' => 'wo.work_number',
            'title' => 'wo.title',
            'description' => 'wo.description',
            'customer_name' => 'c.name',
            'entity_id' => 'wo.entity_id',
            'department_id' => 'wo.department_id',
            'status' => 'wo.status',
            'priority' => 'wo.priority',
            'department_name' => $this->collatedText('d.name'),
            'created_at' => 'wo.created_at',
            'updated_at' => 'wo.updated_at',
        ]);

        $sql = 'SELECT '.$selectList.' FROM work_orders wo '
            .'LEFT JOIN customers c ON c.id = wo.customer_id '
            .'LEFT JOIN departments d ON d.id = wo.department_id';

        return new ProjectionViewDefinition(
            $resourceType,
            $viewName,
            $columns,
            $sql,
            derivedTextColumns: array_values(array_intersect($columns, ['department_name'])),
            atomicReplace: $this->atomicReplaceSupported(),
        );
    }

    private function customerView(string $resourceType): ProjectionViewDefinition
    {
        $viewName = ProjectionViewName::forResource($resourceType);
        $columns = $this->columnsFor($resourceType);
        $selectList = $this->selectListSql($columns, [
            'id' => 'id',
            'name' => 'name',
            'business_name' => 'business_name',
            'phone' => 'phone',
            'email' => 'email',
            'entity_id' => 'entity_id',
            'created_at' => 'created_at',
            'updated_at' => 'updated_at',
        ]);

        $sql = "SELECT {$selectList} FROM customers";

        return new ProjectionViewDefinition($resourceType, $viewName, $columns, $sql, atomicReplace: $this->atomicReplaceSupported());
    }

    private function supplierView(string $resourceType): ProjectionViewDefinition
    {
        $viewName = ProjectionViewName::forResource($resourceType);
        $columns = $this->columnsFor($resourceType);
        $selectList = $this->selectListSql($columns, [
            'id' => 'id',
            'name' => 'name',
            'phone' => 'phone',
            'email' => 'email',
            'entity_id' => 'entity_id',
            'created_at' => 'created_at',
            'updated_at' => 'updated_at',
        ]);

        $sql = "SELECT {$selectList} FROM suppliers";

        return new ProjectionViewDefinition($resourceType, $viewName, $columns, $sql, atomicReplace: $this->atomicReplaceSupported());
    }

    private function productView(string $resourceType): ProjectionViewDefinition
    {
        $viewName = ProjectionViewName::forResource($resourceType);
        $columns = $this->columnsFor($resourceType);
        $selectList = $this->selectListSql($columns, [
            'id' => 'p.id',
            'name' => 'p.name',
            'sku' => 'p.sku',
            'category_name' => 'cat.name',
            'entity_id' => 'p.entity_id',
            'created_at' => 'p.created_at',
            'updated_at' => 'p.updated_at',
        ]);

        $sql = "SELECT {$selectList} FROM products p LEFT JOIN categories cat ON cat.id = p.category_id";

        return new ProjectionViewDefinition($resourceType, $viewName, $columns, $sql, atomicReplace: $this->atomicReplaceSupported());
    }

    private function inventoryView(string $resourceType): ProjectionViewDefinition
    {
        $viewName = ProjectionViewName::forResource($resourceType);
        $columns = $this->columnsFor($resourceType);
        $selectList = $this->selectListSql($columns, [
            'id' => 'i.id',
            'reference' => 'i.reference',
            'description' => 'i.description',
            'supplier_invoice_no' => 'i.supplier_invoice_no',
            'supplier_name' => 's.name',
            'entity_id' => 'i.entity_id',
            'created_at' => 'i.created_at',
            'updated_at' => 'i.updated_at',
        ]);

        $sql = "SELECT {$selectList} FROM inventories i LEFT JOIN suppliers s ON s.id = i.supplier_id";

        return new ProjectionViewDefinition($resourceType, $viewName, $columns, $sql, atomicReplace: $this->atomicReplaceSupported());
    }

    /**
     * Task 1C — the ordered unique union of every column the CURRENT
     * exported manifest addresses for this resource:
     *   - flattened readable_fields;
     *   - declared scope columns (ProjectionScopeColumns — entity/department
     *     + subtype predicate columns + parent FK, see that class);
     *   - record_predicates keys again explicitly (already a subset of scope
     *     above, but spelled out here too so this list stays correct even if
     *     ProjectionScopeColumns' own inclusion of them ever changes);
     *   - manifest filter columns (id, applicable entity/department,
     *     predicates — BusinessRecordDefinition::filterFieldNames(), Task P3,
     *     the SAME derivation PolicyManifestExporter's own filtersFor() now
     *     reads its field names from too, so the two can never drift apart);
     *   - the manifest's sort columns (PolicyManifestExporter::sortFields(),
     *     called STATICALLY — no exporter instance — created_at/updated_at
     *     today).
     *
     * These enforcement-only columns exist for WHERE/ORDER BY; they do not
     * enter field_set_fields and are not returned to the model unless
     * explicitly allowlisted later. Task P3: readable/filter field names are
     * read directly off the registry's own BusinessRecordDefinition now
     * (readableFields()/filterFieldNames(), both pure definition-side
     * methods) rather than through a PolicyManifestExporter instance this
     * class no longer holds — see this class's constructor docblock for why.
     *
     * @return list<string>
     */
    private function columnsFor(string $resourceType): array
    {
        $definition = $this->registry->get($resourceType);

        $readable = array_map(self::outputColumnFor(...), $definition->readableFields());
        $scope = ProjectionScopeColumns::forDefinition($definition);
        $predicates = array_keys($definition->recordPredicates);
        $filters = $definition->filterFieldNames();
        $sorts = PolicyManifestExporter::sortFields();

        return array_values(array_unique([...$readable, ...$scope, ...$predicates, ...$filters, ...$sorts]));
    }

    /**
     * Renders "$expr AS $column" for every column this view declares, in
     * order, pulling each column's source expression out of $sourceByColumn.
     * A column with no expression here is a builder bug — surfaced immediately
     * as a clear exception rather than silently emitting an unaliased NULL.
     *
     * @param  list<string>  $columns
     * @param  array<string, string>  $sourceByColumn
     */
    private function selectListSql(array $columns, array $sourceByColumn): string
    {
        $parts = [];
        foreach ($columns as $column) {
            if (! array_key_exists($column, $sourceByColumn)) {
                throw new InvalidArgumentException(
                    "No source expression mapped for projection column [{$column}]."
                );
            }
            $parts[] = "{$sourceByColumn[$column]} AS {$column}";
        }

        return implode(', ', $parts);
    }
}
