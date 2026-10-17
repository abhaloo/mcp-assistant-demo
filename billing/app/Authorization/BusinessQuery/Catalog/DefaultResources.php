<?php

declare(strict_types=1);

namespace App\Authorization\BusinessQuery\Catalog;

use App\Authorization\BusinessQuery\ResourceBinding;

final class DefaultResources
{
    /** @return list<ResourceBinding> */
    public static function all(): array
    {
        $billView = 'ai_v1_bq_bill_fact';
        $itemView = 'ai_v1_bq_bill_item_fact';
        $entityOnly = ['entity' => 'entity_id', 'department' => null];

        return [
            new ResourceBinding(
                name: 'invoice',
                projectionView: $billView,
                scopeColumns: $entityOnly,
                departmentScopeMode: 'none',
                primaryKey: 'id',
                recordPredicates: ['type' => 'Invoice'],
                description: 'Invoice rows on ai_v1_bq_bill_fact (type=Invoice).',
                recordRoute: 'bill.show',
                recordRouteParam: 'bill',
                linkPermissions: ['view invoice'],
                label: 'Invoice',
            ),
            new ResourceBinding(
                name: 'quotation',
                projectionView: $billView,
                scopeColumns: $entityOnly,
                departmentScopeMode: 'none',
                primaryKey: 'id',
                recordPredicates: ['type' => 'Quotation'],
                description: 'Quotation rows on ai_v1_bq_bill_fact (type=Quotation).',
            ),
            new ResourceBinding(
                name: 'payable_quotation',
                projectionView: $billView,
                scopeColumns: $entityOnly,
                departmentScopeMode: 'none',
                primaryKey: 'id',
                recordPredicates: ['type' => 'Payable Quotation', 'payable' => 'Yes'],
                description: 'Payable Quotation rows (type + payable legs).',
            ),
            new ResourceBinding(
                name: 'invoice_item',
                projectionView: $itemView,
                scopeColumns: $entityOnly,
                departmentScopeMode: 'none',
                primaryKey: 'id',
                recordPredicates: ['bill_type' => 'Invoice'],
                description: 'Invoice line items on ai_v1_bq_bill_item_fact.',
            ),
            new ResourceBinding(
                name: 'quotation_item',
                projectionView: $itemView,
                scopeColumns: $entityOnly,
                departmentScopeMode: 'none',
                primaryKey: 'id',
                recordPredicates: ['bill_type' => 'Quotation'],
                description: 'Quotation line items on ai_v1_bq_bill_item_fact.',
            ),
            new ResourceBinding(
                name: 'payable_quotation_item',
                projectionView: $itemView,
                scopeColumns: $entityOnly,
                departmentScopeMode: 'none',
                primaryKey: 'id',
                recordPredicates: ['bill_type' => 'Payable Quotation', 'bill_payable' => 'Yes'],
                description: 'Payable Quotation line items (both legs).',
            ),
            new ResourceBinding(
                name: 'customer_order',
                projectionView: 'ai_v1_bq_order_fact',
                scopeColumns: $entityOnly,
                departmentScopeMode: 'none',
                primaryKey: 'id',
                recordPredicates: [],
                description: 'Customer orders on ai_v1_bq_order_fact.',
                referenceDimension: 'customer_order.order_number',
            ),
            new ResourceBinding(
                name: 'job',
                projectionView: 'ai_v1_bq_job_fact',
                scopeColumns: ['entity' => 'entity_id', 'department' => 'department_id'],
                departmentScopeMode: 'filter_when_present',
                primaryKey: 'id',
                recordPredicates: [],
                description: 'Work orders / jobs on ai_v1_bq_job_fact.',
                recordRoute: 'work-order.show',
                recordRouteParam: 'work_order',
                linkPermissions: ['view job'],
                label: 'Job',
            ),
            new ResourceBinding(
                name: 'job_print',
                projectionView: 'ai_v2_bq_job_print_fact',
                scopeColumns: ['entity' => 'entity_id', 'department' => 'department_id'],
                departmentScopeMode: 'filter_when_present',
                primaryKey: 'id',
                recordPredicates: [],
                description: 'Print job details and specifications on ai_v2_bq_job_print_fact.',
            ),
            new ResourceBinding(
                name: 'work_order_item',
                projectionView: 'ai_v1_bq_work_order_item_fact',
                scopeColumns: ['entity' => 'entity_id', 'department' => 'department_id'],
                departmentScopeMode: 'filter_when_present',
                primaryKey: 'id',
                recordPredicates: [],
                description: "Material lines PLANNED on a job's INFO-tab planning grid (work_order_items). NOT the MATERIALS tab, which shows issued stock from a different table (inventory_items) that this surface cannot see.",
            ),
            new ResourceBinding(
                name: 'customer',
                projectionView: 'ai_v1_bq_customer_fact',
                scopeColumns: $entityOnly,
                departmentScopeMode: 'none',
                primaryKey: 'id',
                recordPredicates: [],
                description: 'Customers on ai_v1_bq_customer_fact.',
                recordRoute: 'customer.show',
                recordRouteParam: 'customer',
                linkPermissions: ['view customer'],
                label: 'Customer',
            ),
            new ResourceBinding(
                name: 'supplier',
                projectionView: 'ai_v1_bq_supplier_fact',
                scopeColumns: $entityOnly,
                departmentScopeMode: 'none',
                primaryKey: 'id',
                recordPredicates: [],
                description: 'Suppliers on ai_v1_bq_supplier_fact.',
            ),
            new ResourceBinding(
                name: 'ledger_entry',
                projectionView: 'ai_v1_bq_ledger_entry',
                scopeColumns: $entityOnly,
                departmentScopeMode: 'none',
                primaryKey: 'id',
                recordPredicates: [],
                description: 'Journal rows ⋈ accounts. Deliberately NOT kind-scoped: '
                    .'bill_id is nullable (manual journals exist); a kind predicate would '
                    .'silently drop them and change what revenue means. Boundary is the '
                    .'account-type filter plus Financial Analytics grant.',
            ),
            new ResourceBinding(
                name: 'inventory',
                projectionView: 'ai_v1_bq_inventory_fact',
                scopeColumns: $entityOnly,
                departmentScopeMode: 'none',
                primaryKey: 'id',
                recordPredicates: [],
                description: 'Inventory movements on ai_v1_bq_inventory_fact.',
            ),
        ];
    }
}
