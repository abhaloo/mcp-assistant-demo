<?php

namespace App\Actions\Inventory;

use App\Models\Inventory;
use App\Models\InventoryItem;

class CalculateLandedCost
{
    /**
     * Calculate and distribute landed costs across inventory items.
     *
     * Formula:
     * 1. Total Invoice (foreign) = sum of item invoice values
     * 2. Total Charges = clearing + freight + other
     * 3. If charges in USD, convert to foreign: charges / exchange_rate
     * 4. Ratio = (Total Invoice + Total Charges) / Total Invoice
     * 5. Per item: allocated_charges = invoice_value * (ratio - 1)
     * 6. Per item: total_cost_foreign = invoice_value + allocated_charges
     * 7. Per item: total_cost_tzs = total_cost_foreign * exchange_rate
     * 8. Per item: unit_cost_tzs = total_cost_tzs / quantity
     */
    public static function handle(Inventory $inventory): void
    {
        if ($inventory->type !== 'Receive') {
            return;
        }

        $items = $inventory->inventoryItems;

        if ($items->isEmpty()) {
            return;
        }

        $exchangeRate = $inventory->exchange_rate ?: 1;

        // Calculate total invoice value in foreign currency
        $totalInvoiceForeign = $items->sum('invoice_value_foreign');

        // Calculate total charges in foreign currency
        $totalCharges = ($inventory->clearing_charges ?? 0) +
                        ($inventory->freight_charges ?? 0) +
                        ($inventory->other_charges ?? 0);

        // Convert charges to foreign if they're in USD
        if ($inventory->charges_in_tzs && $exchangeRate > 0) {
            $totalCharges = $totalCharges / $exchangeRate;
        }

        // Calculate allocation ratio
        $ratio = $totalInvoiceForeign > 0
            ? ($totalInvoiceForeign + $totalCharges) / $totalInvoiceForeign
            : 1;

        $totalLandedCostTzs = 0;

        // Distribute costs to each item
        foreach ($items as $item) {
            // Skip items with no quantity
            if (($item->quantity ?? 0) <= 0) {
                continue;
            }

            // Calculate allocated charges for this item
            $item->allocated_charges = ($item->invoice_value_foreign ?? 0) * ($ratio - 1);

            // Total cost in foreign currency
            $totalCostForeign = ($item->invoice_value_foreign ?? 0) + $item->allocated_charges;

            // Convert to USD
            $totalCostTzs = $totalCostForeign * $exchangeRate;

            // Unit cost (landed cost per unit)
            $item->unit_cost_tzs = $totalCostTzs / $item->quantity;

            // Also update the main cost field for compatibility
            $item->cost = $item->unit_cost_tzs;

            // Store breakdown for audit
            $item->cost_breakdown = [
                'unit_price_foreign' => $item->unit_price_foreign ?? 0,
                'invoice_value_foreign' => $item->invoice_value_foreign ?? 0,
                'allocated_charges_foreign' => $item->allocated_charges,
                'total_cost_foreign' => $totalCostForeign,
                'exchange_rate' => $exchangeRate,
                'total_cost_tzs' => $totalCostTzs,
                'unit_cost_tzs' => $item->unit_cost_tzs,
                'ratio' => $ratio,
            ];

            $item->save();

            $totalLandedCostTzs += $totalCostTzs;
        }

        // Update inventory total
        $inventory->invoice_value_foreign = $totalInvoiceForeign;
        $inventory->total_landed_cost_tzs = $totalLandedCostTzs;
        $inventory->save();
    }

    /**
     * Recalculate for local suppliers (no foreign currency).
     * Just uses direct USD costs + optional charges.
     */
    public static function handleLocal(Inventory $inventory): void
    {
        if ($inventory->type !== 'Receive') {
            return;
        }

        $items = $inventory->inventoryItems;

        if ($items->isEmpty()) {
            return;
        }

        $totalCharges = ($inventory->clearing_charges ?? 0) +
                        ($inventory->freight_charges ?? 0) +
                        ($inventory->other_charges ?? 0);

        // For local, charges are always in USD
        $totalItemCost = $items->sum(function ($item) {
            return ($item->cost ?? 0) * ($item->quantity ?? 0);
        });

        // Ratio for allocation
        $ratio = $totalItemCost > 0
            ? ($totalItemCost + $totalCharges) / $totalItemCost
            : 1;

        $totalLandedCostTzs = 0;

        foreach ($items as $item) {
            if (($item->quantity ?? 0) <= 0) {
                continue;
            }

            $itemCost = ($item->cost ?? 0) * $item->quantity;
            $item->allocated_charges = $itemCost * ($ratio - 1);
            $item->unit_cost_tzs = ($itemCost + $item->allocated_charges) / $item->quantity;

            // For local, update cost to the landed cost
            $originalCost = $item->cost;
            $item->cost = $item->unit_cost_tzs;

            $item->cost_breakdown = [
                'original_cost' => $originalCost,
                'allocated_charges' => $item->allocated_charges,
                'unit_cost_tzs' => $item->unit_cost_tzs,
                'ratio' => $ratio,
            ];

            $item->save();
            $totalLandedCostTzs += $item->unit_cost_tzs * $item->quantity;
        }

        $inventory->total_landed_cost_tzs = $totalLandedCostTzs;
        $inventory->save();
    }

    /**
     * Preview landed costs without saving (for real-time UI calculation).
     *
     * @param  array  $inventoryData  Inventory header data
     * @param  array  $items  Array of item data
     * @return array Calculated preview data
     */
    public static function preview(array $inventoryData, array $items): array
    {
        $exchangeRate = $inventoryData['exchange_rate'] ?? 1;
        $chargesInTzs = $inventoryData['charges_in_tzs'] ?? false;

        $totalInvoiceForeign = collect($items)->sum('invoice_value_foreign');

        $totalCharges = ($inventoryData['clearing_charges'] ?? 0) +
                        ($inventoryData['freight_charges'] ?? 0) +
                        ($inventoryData['other_charges'] ?? 0);

        if ($chargesInTzs && $exchangeRate > 0) {
            $totalCharges = $totalCharges / $exchangeRate;
        }

        $ratio = $totalInvoiceForeign > 0
            ? ($totalInvoiceForeign + $totalCharges) / $totalInvoiceForeign
            : 1;

        $calculatedItems = [];
        $totalLandedCostTzs = 0;

        foreach ($items as $key => $item) {
            $quantity = $item['quantity'] ?? 0;
            $invoiceValueForeign = $item['invoice_value_foreign'] ?? 0;

            if ($quantity <= 0) {
                $calculatedItems[$key] = $item;

                continue;
            }

            $allocatedCharges = $invoiceValueForeign * ($ratio - 1);
            $totalCostForeign = $invoiceValueForeign + $allocatedCharges;
            $totalCostTzs = $totalCostForeign * $exchangeRate;
            $unitCostTzs = $totalCostTzs / $quantity;

            $calculatedItems[$key] = array_merge($item, [
                'allocated_charges' => round($allocatedCharges, 2),
                'total_cost_foreign' => round($totalCostForeign, 2),
                'total_cost_tzs' => round($totalCostTzs, 2),
                'unit_cost_tzs' => round($unitCostTzs, 4),
                'cost' => round($unitCostTzs, 4),
            ]);

            $totalLandedCostTzs += $totalCostTzs;
        }

        return [
            'items' => $calculatedItems,
            'ratio' => round($ratio, 4),
            'total_invoice_foreign' => round($totalInvoiceForeign, 2),
            'total_charges_foreign' => round($totalCharges, 2),
            'total_landed_cost_tzs' => round($totalLandedCostTzs, 2),
        ];
    }

    /**
     * Calculate cost breakdown at multiple unit levels.
     * Useful for displaying "cost per pallet", "cost per ream", "cost per sheet" etc.
     *
     * @param  InventoryItem  $item
     * @param  float  $totalCostTzs  Total landed cost in USD for this item
     * @return array Breakdown showing costs at each unit level
     */
    public static function calculateUnitBreakdown($item, float $totalCostTzs): array
    {
        $product = $item->product;
        $category = $product?->category;

        $quantity = $item->quantity; // Stock units (e.g., sheets)
        $costingToIntermediate = $item->costing_to_intermediate ?? $product?->costing_to_intermediate ?? 1;
        $intermediateToStock = $item->intermediate_to_stock ?? $product?->intermediate_to_stock ?? 1;

        $breakdown = [
            'stock_quantity' => $quantity,
            'stock_unit_label' => $category?->getStockUnit()['label'] ?? $product?->unit?->name ?? 'Unit',
            'cost_per_stock_unit' => $quantity > 0 ? $totalCostTzs / $quantity : 0,
        ];

        // Calculate intermediate level (e.g., reams)
        if ($intermediateToStock > 0 && $category?->hasIntermediateUnit()) {
            $intermediateQuantity = $quantity / $intermediateToStock;
            $breakdown['intermediate_quantity'] = $intermediateQuantity;
            $breakdown['intermediate_unit_label'] = $category->getIntermediateUnit()['label'];
            $breakdown['cost_per_intermediate'] = $intermediateQuantity > 0 ? $totalCostTzs / $intermediateQuantity : 0;
        }

        // Calculate costing level (e.g., pallets)
        if ($costingToIntermediate > 0 && $intermediateToStock > 0) {
            $costingQuantity = $quantity / ($costingToIntermediate * $intermediateToStock);
            $breakdown['costing_quantity'] = $costingQuantity;
            $breakdown['costing_unit_label'] = $category?->getCostingUnit()['label'] ?? 'Unit';
            $breakdown['cost_per_costing_unit'] = $costingQuantity > 0 ? $totalCostTzs / $costingQuantity : 0;
        }

        return $breakdown;
    }
}
