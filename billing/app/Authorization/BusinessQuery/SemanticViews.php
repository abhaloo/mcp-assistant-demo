<?php

declare(strict_types=1);

namespace App\Authorization\BusinessQuery;

use Illuminate\Database\Connection;
use InvalidArgumentException;

/**
 * Dedicated ai_v1_bq_* semantic fact views for the business-query layer.
 *
 * Decoupled from the policy manifest / ProjectionDefinitions on purpose:
 * these views carry money and scope columns the record-tools claim surface
 * must never widen. Authorization is the capability card + scope pass, not
 * the v2 claim payload.
 *
 * days_past_due anchors to the DB session date (MySQL CURRENT_DATE /
 * SQLite julianday('now','localtime','start of day')). That may differ from
 * the bundle-timezone "today" the RAG compiler uses for relative periods by
 * at most one day around midnight.
 */
final class SemanticViews
{
    /** Qualifying payment account types — mirrors Bill::payments() / ProjectionDefinitions. */
    private const QUALIFYING_ACCOUNT_TYPES = ['OPERATING_EXPENSE', 'PAYABLE', 'CASH', 'BANK'];

    /** @var array<string, list<string>> */
    public const COLUMNS = [
        'ai_v1_bq_bill_fact' => [
            'id', 'invoice_number', 'customer_id', 'customer_name', 'customer_order_id',
            'customer_order_number', 'type', 'payable',
            'status', 'created_at', 'due_date', 'currency_code', 'items_total', 'cash_received',
            'receivable_settlement', 'outstanding', 'days_past_due', 'entity_id',
        ],
        'ai_v1_bq_order_fact' => [
            'id', 'order_number', 'description', 'customer_id', 'customer_name', 'status',
            'computed_status', 'created_at', 'entity_id',
        ],
        'ai_v1_bq_job_fact' => [
            'id', 'customer_order_id', 'customer_order_number', 'customer_id', 'customer_name',
            'title', 'work_number', 'ordered_qty',
            'effective_bill_id', 'status', 'department_id', 'department_name', 'created_at', 'entity_id',
        ],
        'ai_v1_bq_customer_fact' => [
            'id', 'name', 'has_orders', 'created_at', 'entity_id',
        ],
        'ai_v1_bq_supplier_fact' => [
            'id', 'name', 'created_at', 'entity_id',
        ],
        'ai_v1_bq_ledger_entry' => [
            'id', 'bill_id', 'currency_code', 'account_type', 'debit', 'credit', 'post_date', 'entity_id',
        ],
        'ai_v1_bq_bill_item_fact' => [
            'id', 'bill_id', 'invoice_number', 'product_id', 'product_name', 'quantity', 'price', 'discount', 'tax', 'item_revenue',
            'currency_code', 'bill_type', 'bill_payable', 'bill_created_at', 'entity_id',
        ],
        'ai_v1_bq_inventory_fact' => [
            'id', 'supplier_id', 'job_id', 'type', 'status', 'inventory_date', 'created_at', 'supplier_name', 'warehouse_name', 'entity_id',
        ],
        'ai_v1_bq_work_order_item_fact' => [
            'id', 'work_order_id', 'product_id', 'product_name', 'quantity', 'used_at',
            'department_id', 'entity_id',
        ],
        'ai_v2_bq_job_print_fact' => [
            'id', 'job_id', 'work_order_id', 'work_number', 'title', 'customer_id', 'customer_order_id',
            'department_id', 'department_name', 'status', 'created_at', 'entity_id',
            'resource_type', 'family_key', 'attribute_key', 'revision', 'revision_hash',
            'value_kind', 'typed_value', 'value_text', 'value_number', 'value_boolean', 'value_json',
            'display_value', 'validation_state', 'source', 'provenance',
        ],
        'ai_v2_bq_detail_definition' => [
            'id', 'entity_id', 'resource_type', 'family_key', 'attribute_key', 'revision',
            'revision_hash', 'value_kind', 'data_type', 'label', 'aliases', 'unit', 'options',
            'is_active', 'published_at', 'retired_at', 'metadata',
        ],
        'ai_v2_bq_detail_attribute_fact' => [
            'id', 'resource_type', 'resource_id', 'entity_id', 'department_id', 'family_key',
            'attribute_key', 'revision', 'revision_hash', 'value_kind', 'typed_value',
            'value_text', 'value_number', 'value_boolean', 'value_json', 'display_value',
            'validation_state', 'source', 'provenance', 'raw_value', 'source_fingerprint',
            'source_updated_at',
        ],
    ];

    public function __construct(
        private readonly string $driver = 'mysql',
    ) {}

    public static function apply(Connection $connection): void
    {
        $views = new self($connection->getDriverName());

        foreach (array_keys(self::COLUMNS) as $viewName) {
            foreach ($views->applyStatements($viewName) as $statement) {
                $connection->statement($statement);
            }
        }
    }

    /** @return list<string> */
    public function columnsFor(string $viewName): array
    {
        if (! array_key_exists($viewName, self::COLUMNS)) {
            throw new InvalidArgumentException("Unknown semantic view [{$viewName}].");
        }

        return self::COLUMNS[$viewName];
    }

    /**
     * Driver-aware days_past_due expression. Both operands are midnight-anchored
     * so SQLite CAST(... AS INTEGER) agrees with MySQL DATEDIFF on signed values.
     * The SQLite 'localtime' modifier aligns 'now' (UTC) with PHP-local due_date
     * seeds and MySQL's session-local CURRENT_DATE.
     */
    public function daysPastDueExpression(string $dueDateColumn = 'b.due_date'): string
    {
        if ($this->driver === 'sqlite') {
            return "CAST(julianday('now', 'localtime', 'start of day') - julianday(date({$dueDateColumn})) AS INTEGER)";
        }

        return "DATEDIFF(CURRENT_DATE, {$dueDateColumn})";
    }

    public function selectSql(string $viewName): string
    {
        return match ($viewName) {
            'ai_v1_bq_bill_fact' => $this->billFactSql(),
            'ai_v1_bq_order_fact' => $this->orderFactSql(),
            'ai_v1_bq_job_fact' => $this->jobFactSql(),
            'ai_v1_bq_customer_fact' => $this->customerFactSql(),
            'ai_v1_bq_supplier_fact' => $this->supplierFactSql(),
            'ai_v1_bq_ledger_entry' => $this->ledgerEntrySql(),
            'ai_v1_bq_bill_item_fact' => $this->billItemFactSql(),
            'ai_v1_bq_inventory_fact' => $this->inventoryFactSql(),
            'ai_v1_bq_work_order_item_fact' => $this->workOrderItemFactSql(),
            'ai_v2_bq_job_print_fact' => $this->jobPrintFactSql(),
            'ai_v2_bq_detail_definition' => $this->detailDefinitionSql(),
            'ai_v2_bq_detail_attribute_fact' => $this->detailAttributeFactSql(),
            default => throw new InvalidArgumentException("Unknown semantic view [{$viewName}]."),
        };
    }

    /** @return list<string> */
    public function applyStatements(string $viewName): array
    {
        $sql = $this->selectSql($viewName);

        if ($this->driver !== 'sqlite') {
            return ["CREATE OR REPLACE VIEW {$viewName} AS {$sql}"];
        }

        return [
            "DROP VIEW IF EXISTS {$viewName}",
            "CREATE VIEW {$viewName} AS {$sql}",
        ];
    }

    private function collatedText(string $expr): string
    {
        return $this->driver === 'sqlite'
            ? "CAST(({$expr}) AS TEXT)"
            : "CAST(({$expr}) AS CHAR CHARACTER SET utf8mb4) COLLATE utf8mb4_unicode_ci";
    }

    private function integerCast(string $expr): string
    {
        return $this->driver === 'sqlite'
            ? "CAST(({$expr}) AS INTEGER)"
            : "CAST(({$expr}) AS UNSIGNED)";
    }

    private function billItemValueExpression(): string
    {
        return '(CAST(bi.price AS DOUBLE) * CAST(bi.quantity AS DOUBLE) - CAST(bi.discount AS DOUBLE))';
    }

    private function qualifyingAccountTypesSql(): string
    {
        return implode(', ', array_map(
            static fn (string $type): string => "'{$type}'",
            self::QUALIFYING_ACCOUNT_TYPES,
        ));
    }

    private function itemsTotalAggregateSql(): string
    {
        $itemValue = $this->billItemValueExpression();

        return 'SELECT bi.bill_id AS bill_id, '
            ."SUM({$itemValue} + ({$itemValue} * (CAST(bi.tax AS DOUBLE) / 100))) AS items_total "
            .'FROM bill_items bi GROUP BY bi.bill_id';
    }

    private function journalsAggregateSql(): string
    {
        $qualifyingTypes = $this->qualifyingAccountTypesSql();

        return 'SELECT j.bill_id AS bill_id, '
            .'SUM(CAST(j.debit AS DOUBLE)) AS receivable_settlement, '
            ."SUM(CASE WHEN a.account_type IN ('CASH', 'BANK') THEN CAST(j.debit AS DOUBLE) ELSE 0 END) AS cash_received "
            .'FROM journals j '
            .'INNER JOIN accounts a ON a.id = j.account_id '
            ."WHERE j.debit > 0 AND a.account_type IN ({$qualifyingTypes}) "
            .'GROUP BY j.bill_id';
    }

    private function billFactSql(): string
    {
        $itemsTotal = 'COALESCE(items_agg.items_total, 0)';
        $receivableSettlement = 'COALESCE(journals_agg.receivable_settlement, 0)';
        $cashReceived = 'COALESCE(journals_agg.cash_received, 0)';
        $daysPastDue = $this->daysPastDueExpression('b.due_date');
        $customerName = $this->collatedText('c.name');

        return 'SELECT '
            .'b.id AS id, '
            .'b.invoice_number AS invoice_number, '
            .'b.customer_id AS customer_id, '
            ."{$customerName} AS customer_name, "
            .'b.customer_order_id AS customer_order_id, '
            .'bco.order_number AS customer_order_number, '
            .'b.type AS type, '
            .'b.payable AS payable, '
            .'b.status AS status, '
            .'b.created_at AS created_at, '
            .'b.due_date AS due_date, '
            .'cny.currency_code AS currency_code, '
            ."{$itemsTotal} AS items_total, "
            ."{$cashReceived} AS cash_received, "
            ."{$receivableSettlement} AS receivable_settlement, "
            ."(({$itemsTotal}) - ({$receivableSettlement})) AS outstanding, "
            ."{$daysPastDue} AS days_past_due, "
            .'b.entity_id AS entity_id '
            .'FROM bills b '
            .'LEFT JOIN customers c ON c.id = b.customer_id '
            .'LEFT JOIN customer_orders bco ON bco.id = b.customer_order_id AND bco.entity_id = b.entity_id '
            .'LEFT JOIN currencies cny ON cny.id = b.currency_id '
            .'LEFT JOIN ('.$this->itemsTotalAggregateSql().') items_agg ON items_agg.bill_id = b.id '
            .'LEFT JOIN ('.$this->journalsAggregateSql().') journals_agg ON journals_agg.bill_id = b.id';
    }

    private function orderFactSql(): string
    {
        $customerName = $this->collatedText('c.name');

        // Bit-for-bit CustomerOrder::getComputedStatusAttribute(): CANCELLED and
        // FINISHED pass through; otherwise NEW until any work order exists, then
        // IN_PROGRESS. The app DISPLAYS this computed value, never the stored one.
        $computedStatus = "CASE WHEN co.status = 'CANCELLED' THEN 'CANCELLED' "
            ."WHEN co.status = 'FINISHED' THEN 'FINISHED' "
            .'WHEN EXISTS (SELECT 1 FROM work_orders wo WHERE wo.customer_order_id = co.id) '
            ."THEN 'IN_PROGRESS' ELSE 'NEW' END";

        return 'SELECT '
            .'co.id AS id, '
            .'co.order_number AS order_number, '
            .'co.description AS description, '
            .'co.customer_id AS customer_id, '
            ."{$customerName} AS customer_name, "
            .'co.status AS status, '
            .$this->collatedText($computedStatus).' AS computed_status, '
            .'co.created_at AS created_at, '
            .'co.entity_id AS entity_id '
            .'FROM customer_orders co '
            .'LEFT JOIN customers c ON c.id = co.customer_id';
    }

    private function jobFactSql(): string
    {
        $departmentName = $this->collatedText('d.name');
        $customerName = $this->collatedText('c.name');
        $title = $this->collatedText('wo.title');
        $workNumber = $this->collatedText('wo.work_number');

        $effectiveBillId = "COALESCE((SELECT b.id FROM bills b WHERE b.id = wo.bill_id AND b.type = 'Invoice'), "
            .'(SELECT MAX(bco.id) FROM bills bco '
            ."WHERE bco.customer_order_id = wo.customer_order_id AND bco.type = 'Invoice'))";

        return 'SELECT '
            .'wo.id AS id, '
            .'wo.customer_order_id AS customer_order_id, '
            .'co.order_number AS customer_order_number, '
            .'wo.customer_id AS customer_id, '
            ."{$customerName} AS customer_name, "
            ."{$title} AS title, "
            ."{$workNumber} AS work_number, "
            .'wo.ordered_qty AS ordered_qty, '
            ."{$effectiveBillId} AS effective_bill_id, "
            .'wo.status AS status, '
            .'wo.department_id AS department_id, '
            ."{$departmentName} AS department_name, "
            .'wo.created_at AS created_at, '
            .'wo.entity_id AS entity_id '
            .'FROM work_orders wo '
            .'LEFT JOIN customer_orders co ON co.id = wo.customer_order_id AND co.entity_id = wo.entity_id '
            .'LEFT JOIN customers c ON c.id = wo.customer_id '
            .'LEFT JOIN departments d ON d.id = wo.department_id';
    }

    private function supplierFactSql(): string
    {
        return 'SELECT '
            .'s.id AS id, '
            .'s.name AS name, '
            .'s.created_at AS created_at, '
            .'s.entity_id AS entity_id '
            .'FROM suppliers s';
    }

    private function customerFactSql(): string
    {
        $hasOrders = 'CASE WHEN EXISTS ('
            .'SELECT 1 FROM customer_orders co WHERE co.customer_id = c.id'
            .') THEN 1 ELSE 0 END';

        return 'SELECT '
            .'c.id AS id, '
            .'c.name AS name, '
            ."{$hasOrders} AS has_orders, "
            .'c.created_at AS created_at, '
            .'c.entity_id AS entity_id '
            .'FROM customers c';
    }

    private function ledgerEntrySql(): string
    {
        $accountType = $this->collatedText('a.account_type');

        // PostEntry::handle() always posts journal amounts unconverted in the
        // entity's home currency, regardless of the bill. The currency LABEL
        // must still reflect the bill actually being paid: bill-tied entries
        // resolve to the bill's own currency; non-bill (manual) journals keep
        // their own currency_id.
        return 'SELECT '
            .'j.id AS id, '
            .'j.bill_id AS bill_id, '
            .'cny.currency_code AS currency_code, '
            ."{$accountType} AS account_type, "
            .'j.debit AS debit, '
            .'j.credit AS credit, '
            .'j.post_date AS post_date, '
            .'j.entity_id AS entity_id '
            .'FROM journals j '
            .'LEFT JOIN accounts a ON a.id = j.account_id '
            .'LEFT JOIN bills b ON b.id = j.bill_id '
            .'LEFT JOIN currencies cny ON cny.id = COALESCE(b.currency_id, j.currency_id)';
    }

    private function billItemFactSql(): string
    {
        // Bit-for-bit BillPaymentState::itemsTotal() grouping — not ·(1+tax/100).
        $itemValue = $this->billItemValueExpression();
        $itemRevenue = "({$itemValue} + ({$itemValue} * (CAST(bi.tax AS DOUBLE) / 100)))";
        $productName = $this->collatedText('p.name');

        return 'SELECT '
            .'bi.id AS id, '
            .'bi.bill_id AS bill_id, '
            .'b.invoice_number AS invoice_number, '
            .'bi.product_id AS product_id, '
            ."{$productName} AS product_name, "
            .'bi.quantity AS quantity, '
            .'bi.price AS price, '
            .'bi.discount AS discount, '
            .'bi.tax AS tax, '
            ."{$itemRevenue} AS item_revenue, "
            .'cny.currency_code AS currency_code, '
            .'b.type AS bill_type, '
            .'b.payable AS bill_payable, '
            .'b.created_at AS bill_created_at, '
            .'b.entity_id AS entity_id '
            .'FROM bill_items bi '
            .'INNER JOIN bills b ON b.id = bi.bill_id '
            .'LEFT JOIN currencies cny ON cny.id = b.currency_id '
            .'LEFT JOIN products p ON p.id = bi.product_id';
    }

    private function inventoryFactSql(): string
    {
        $supplierName = $this->collatedText('s.name');
        $warehouseName = $this->collatedText('w.name');
        $jobId = $this->integerCast('i.work_order_id');

        return 'SELECT '
            .'i.id AS id, '
            .'i.supplier_id AS supplier_id, '
            ."{$jobId} AS job_id, "
            .'i.type AS type, '
            .'i.status AS status, '
            .'i.inventory_date AS inventory_date, '
            .'i.created_at AS created_at, '
            ."{$supplierName} AS supplier_name, "
            ."{$warehouseName} AS warehouse_name, "
            .'i.entity_id AS entity_id '
            .'FROM inventories i '
            .'LEFT JOIN suppliers s ON s.id = i.supplier_id '
            .'LEFT JOIN ware_houses w ON w.id = i.ware_house_id';
    }

    /**
     * Materials CONSUMED by a job, the production-side counterpart of
     * billItemFactSql's sold lines.
     *
     * work_order_items carries neither entity_id nor department_id, and both are
     * scope boundaries, so each is inherited from the parent work order exactly
     * as bill_items inherits entity_id from its bill. The join is INNER, not
     * LEFT: a line whose parent is missing has no scope at all and must be
     * invisible rather than NULL-scoped. Three such rows exist in the 2026-08
     * snapshot, so totals will not tie to a raw count of the base table.
     */
    private function workOrderItemFactSql(): string
    {
        $productName = $this->collatedText('p.name');

        return 'SELECT '
            .'wi.id AS id, '
            .'wi.work_order_id AS work_order_id, '
            .'wi.product_id AS product_id, '
            ."{$productName} AS product_name, "
            .'wi.quantity AS quantity, '
            .'wi.created_at AS used_at, '
            .'wo.department_id AS department_id, '
            .'wo.entity_id AS entity_id '
            .'FROM work_order_items wi '
            .'INNER JOIN work_orders wo ON wo.id = wi.work_order_id '
            .'LEFT JOIN products p ON p.id = wi.product_id';
    }

    private function jobPrintFactSql(): string
    {
        $departmentName = $this->collatedText('d.name');
        $title = $this->collatedText('wo.title');
        $workNumber = $this->collatedText('wo.work_number');

        return 'SELECT '
            .'wo.id AS id, '
            .'wo.id AS job_id, '
            .'adv.work_order_id AS work_order_id, '
            ."{$workNumber} AS work_number, "
            ."{$title} AS title, "
            .'wo.customer_id AS customer_id, '
            .'wo.customer_order_id AS customer_order_id, '
            .'wo.department_id AS department_id, '
            ."{$departmentName} AS department_name, "
            .'wo.status AS status, '
            .'wo.created_at AS created_at, '
            .'wo.entity_id AS entity_id, '
            .'dr.resource_type AS resource_type, '
            .'af.name AS family_key, '
            .'dr.attribute_key AS attribute_key, '
            .'dr.revision AS revision, '
            .'adv.revision_hash AS revision_hash, '
            .'dr.value_kind AS value_kind, '
            .'adv.value_text AS typed_value, '
            .'adv.value_text AS value_text, '
            .'adv.value_number AS value_number, '
            .'adv.value_boolean AS value_boolean, '
            .'adv.value_json AS value_json, '
            .'adv.display_value AS display_value, '
            .'adv.validation_state AS validation_state, '
            .'adv.source AS source, '
            .'adv.provenance AS provenance '
            .'FROM work_orders wo '
            .'INNER JOIN ai_job_detail_values adv ON adv.work_order_id = wo.id '
            .'INNER JOIN ai_definition_revisions dr ON dr.id = adv.definition_revision_id '
            .'AND dr.revision_hash = adv.revision_hash '
            .'INNER JOIN ai_attribute_families af ON af.id = dr.attribute_family_id '
            ."AND af.name = 'print.sides' "
            ."AND dr.attribute_key = 'print.sides' "
            .'LEFT JOIN departments d ON d.id = wo.department_id';
    }

    /**
     * Resource-neutral published definition projection. The entity join is
     * deliberate: historical rows with a mismatched default tenant must never
     * become an authorized definition for another tenant.
     */
    private function detailDefinitionSql(): string
    {
        return 'SELECT '
            .'dr.id AS id, '
            .'dr.entity_id AS entity_id, '
            .'dr.resource_type AS resource_type, '
            .'af.name AS family_key, '
            .'dr.attribute_key AS attribute_key, '
            .'dr.revision AS revision, '
            .'dr.revision_hash AS revision_hash, '
            .'dr.value_kind AS value_kind, '
            .'dr.data_type AS data_type, '
            .'dr.label AS label, '
            .'dr.aliases AS aliases, '
            .'dr.unit AS unit, '
            .'dr.options AS options, '
            .'dr.is_active AS is_active, '
            .'dr.published_at AS published_at, '
            .'dr.retired_at AS retired_at, '
            .'dr.metadata AS metadata '
            .'FROM ai_definition_revisions dr '
            .'INNER JOIN ai_attribute_families af '
            .'ON af.id = dr.attribute_family_id AND af.entity_id = dr.entity_id';
    }

    /**
     * Resource-neutral typed observation projection. Current typed values are
     * parent-owned by WorkOrder, but the exported owner key is deliberately
     * named resource_id so the source contract does not bake in a job-only
     * column name. Parent/entity equality is part of the source predicate.
     */
    private function detailAttributeFactSql(): string
    {
        return 'SELECT '
            .'adv.id AS id, '
            .'dr.resource_type AS resource_type, '
            .'adv.work_order_id AS resource_id, '
            .'adv.entity_id AS entity_id, '
            .'adv.department_id AS department_id, '
            .'af.name AS family_key, '
            .'dr.attribute_key AS attribute_key, '
            .'dr.revision AS revision, '
            .'adv.revision_hash AS revision_hash, '
            .'dr.value_kind AS value_kind, '
            .'adv.value_text AS typed_value, '
            .'adv.value_text AS value_text, '
            .'adv.value_number AS value_number, '
            .'adv.value_boolean AS value_boolean, '
            .'adv.value_json AS value_json, '
            .'adv.display_value AS display_value, '
            .'adv.validation_state AS validation_state, '
            .'adv.source AS source, '
            .'adv.provenance AS provenance, '
            .'adv.raw_value AS raw_value, '
            .'adv.source_fingerprint AS source_fingerprint, '
            .'adv.source_updated_at AS source_updated_at '
            .'FROM ai_job_detail_values adv '
            .'INNER JOIN work_orders wo '
            .'ON wo.id = adv.work_order_id AND wo.entity_id = adv.entity_id '
            .'INNER JOIN ai_definition_revisions dr '
            .'ON dr.id = adv.definition_revision_id '
            .'AND dr.revision_hash = adv.revision_hash '
            .'AND dr.entity_id = adv.entity_id '
            .'INNER JOIN ai_attribute_families af '
            .'ON af.id = dr.attribute_family_id AND af.entity_id = adv.entity_id';
    }
}
