<?php

declare(strict_types=1);

namespace App\Authorization\BusinessQuery\Catalog;

use App\Authorization\BusinessQuery\JoinDefinition;

final class DefaultJoins
{
    /** @return list<JoinDefinition> */
    public static function all(): array
    {
        return [
            new JoinDefinition('customer', 'customer_order', 'one_to_many', 'id = customer_id'),
            new JoinDefinition('customer', 'invoice', 'one_to_many', 'id = customer_id'),
            // work_orders.customer_order_id is nullable — bill-less/order-less direct jobs
            // exist by design (see BusinessDefinitionRegistryTest's join-graph pin for the
            // migration citation). Optional: a job-owned measure joined through this edge
            // must refuse rather than silently drop direct jobs from the aggregate.
            new JoinDefinition('customer_order', 'job', 'one_to_many', 'id = customer_order_id', optional: true),
            new JoinDefinition('invoice', 'invoice_item', 'one_to_many', 'id = bill_id'),
            new JoinDefinition('invoice', 'ledger_entry', 'one_to_many', 'id = bill_id', optional: true),
            // work_orders.customer_id is NOT NULL. Direct 1-hop edge so "jobs by customer"
            // is answerable (BFS shortest-path prefers it over the 2-hop customer_order
            // route) without losing direct jobs to the optional edge above.
            new JoinDefinition('customer', 'job', 'one_to_many', 'id = customer_id'),
            // Non-optional: work_order_items.work_order_id is NOT NULL, so no line is
            // dropped, and a measure owned by an OPTIONAL join child is refused outright
            // (optional_join_unsupported) -- product_units_used needs this edge required.
            new JoinDefinition('job', 'work_order_item', 'one_to_many', 'id = work_order_id'),
            // bills.customer_order_id IS nullable
            // (migrations/2025_12_16_170310_create_customer_orders_table.php:30-33 --
            // foreignId('customer_order_id')->nullable()): direct, order-less invoices
            // exist by design, so this edge is optional for the same reason as
            // customer_order->job above.
            new JoinDefinition('customer_order', 'invoice', 'one_to_many', 'id = customer_order_id', optional: true),
            // Edges the semantic layer walks for linked records (quotation and payable
            // quotation lines, jobs billed through an invoice, inventory by supplier or job).
            new JoinDefinition('customer', 'quotation', 'one_to_many', 'id = customer_id', optional: true),
            new JoinDefinition('customer', 'payable_quotation', 'one_to_many', 'id = customer_id', optional: true),
            new JoinDefinition('customer_order', 'quotation', 'one_to_many', 'id = customer_order_id', optional: true),
            new JoinDefinition('quotation', 'quotation_item', 'one_to_many', 'id = bill_id'),
            new JoinDefinition('payable_quotation', 'payable_quotation_item', 'one_to_many', 'id = bill_id'),
            new JoinDefinition('invoice', 'job', 'one_to_many', 'id = effective_bill_id', optional: true),
            new JoinDefinition('supplier', 'inventory', 'one_to_many', 'id = supplier_id', optional: true),
            new JoinDefinition('job', 'inventory', 'one_to_many', 'id = job_id', optional: true),
        ];
    }
}
