<?php

namespace App\Actions\Inventory;

use App\Models\InventoryItem;
use App\Models\Product;
use Illuminate\Support\Facades\DB;

class CalculateWeightedAverageCost
{
    /**
     * Calculate weighted average cost for a product.
     *
     * Formula: WAC = Total Cost of Goods Available / Total Units Available
     *
     * Only considers APPROVED Receive entries.
     *
     * @param  int|null  $warehouseId  Optional warehouse filter
     */
    public static function handle(Product $product, ?int $warehouseId = null): float
    {
        $query = InventoryItem::query()
            ->where('product_id', $product->id)
            ->whereHas('inventory', function ($q) use ($warehouseId) {
                $q->where('type', 'Receive')
                    ->where('status', 'APPROVED');

                if ($warehouseId) {
                    $q->where('ware_house_id', $warehouseId);
                }
            });

        $totalCost = (float) $query->sum(DB::raw('cost * quantity'));
        $totalQuantity = (float) $query->sum('quantity');

        if ($totalQuantity <= 0) {
            return $product->price ?? 0;
        }

        return round($totalCost / $totalQuantity, 4);
    }

    /**
     * Get weighted average cost at a specific date.
     *
     * @param  string  $date  Date in Y-m-d format
     * @param  int|null  $warehouseId  Optional warehouse filter
     */
    public static function atDate(Product $product, string $date, ?int $warehouseId = null): float
    {
        $query = InventoryItem::query()
            ->where('product_id', $product->id)
            ->whereHas('inventory', function ($q) use ($warehouseId, $date) {
                $q->where('type', 'Receive')
                    ->where('status', 'APPROVED')
                    ->where('inventory_date', '<=', $date);

                if ($warehouseId) {
                    $q->where('ware_house_id', $warehouseId);
                }
            });

        $totalCost = (float) $query->sum(DB::raw('cost * quantity'));
        $totalQuantity = (float) $query->sum('quantity');

        if ($totalQuantity <= 0) {
            return $product->price ?? 0;
        }

        return round($totalCost / $totalQuantity, 4);
    }

    /**
     * Get weighted average cost within a date range.
     */
    public static function forPeriod(
        Product $product,
        string $startDate,
        string $endDate,
        ?int $warehouseId = null
    ): float {
        $query = InventoryItem::query()
            ->where('product_id', $product->id)
            ->whereHas('inventory', function ($q) use ($warehouseId, $startDate, $endDate) {
                $q->where('type', 'Receive')
                    ->where('status', 'APPROVED')
                    ->whereBetween('inventory_date', [$startDate, $endDate]);

                if ($warehouseId) {
                    $q->where('ware_house_id', $warehouseId);
                }
            });

        $totalCost = (float) $query->sum(DB::raw('cost * quantity'));
        $totalQuantity = (float) $query->sum('quantity');

        if ($totalQuantity <= 0) {
            // Fall back to overall WAC if no receipts in period
            return self::atDate($product, $endDate, $warehouseId);
        }

        return round($totalCost / $totalQuantity, 4);
    }

    /**
     * Get cost breakdown for a product showing all receive entries.
     */
    public static function getBreakdown(Product $product, ?int $warehouseId = null, int $limit = 10): array
    {
        $items = InventoryItem::query()
            ->where('product_id', $product->id)
            ->whereHas('inventory', function ($q) use ($warehouseId) {
                $q->where('type', 'Receive')
                    ->where('status', 'APPROVED');

                if ($warehouseId) {
                    $q->where('ware_house_id', $warehouseId);
                }
            })
            ->with(['inventory.supplier', 'inventory.currency'])
            ->orderBy('id', 'desc')
            ->limit($limit)
            ->get();

        $breakdown = [];
        foreach ($items as $item) {
            $breakdown[] = [
                'date' => $item->inventory->inventory_date?->format('Y-m-d'),
                'supplier' => $item->inventory->supplier?->name ?? 'Unknown',
                'reference' => $item->inventory->reference,
                'quantity' => $item->quantity,
                'unit_cost' => $item->cost,
                'total_cost' => $item->quantity * $item->cost,
                'currency' => $item->inventory->currency?->currency_code ?? 'USD',
                'exchange_rate' => $item->inventory->exchange_rate ?? 1,
            ];
        }

        return $breakdown;
    }

    /**
     * Calculate and update product's price field with current WAC.
     *
     * @return float The updated WAC
     */
    public static function updateProductPrice(Product $product, ?int $warehouseId = null): float
    {
        $wac = self::handle($product, $warehouseId);

        if ($wac > 0) {
            $product->update(['price' => $wac]);
        }

        return $wac;
    }

    /**
     * Batch calculate WAC for multiple products.
     *
     * @return array [product_id => wac]
     */
    public static function batchCalculate(array $productIds, ?int $warehouseId = null): array
    {
        $results = [];

        $query = InventoryItem::query()
            ->select('product_id')
            ->selectRaw('SUM(cost * quantity) as total_cost')
            ->selectRaw('SUM(quantity) as total_quantity')
            ->whereIn('product_id', $productIds)
            ->whereHas('inventory', function ($q) use ($warehouseId) {
                $q->where('type', 'Receive')
                    ->where('status', 'APPROVED');

                if ($warehouseId) {
                    $q->where('ware_house_id', $warehouseId);
                }
            })
            ->groupBy('product_id')
            ->get();

        foreach ($query as $row) {
            $results[$row->product_id] = $row->total_quantity > 0
                ? round($row->total_cost / $row->total_quantity, 4)
                : 0;
        }

        // Fill in zeros for products with no receipts
        foreach ($productIds as $id) {
            if (! isset($results[$id])) {
                $results[$id] = 0;
            }
        }

        return $results;
    }
}
