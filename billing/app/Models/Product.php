<?php

namespace App\Models;

use App\Models\Concerns\LogsModelActivity;
use App\Traits\EntityTrait;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Session;
use PhpOption\Option;

class Product extends Model
{
    use EntityTrait, HasFactory, LogsModelActivity;

    /** @var array<string, float|int> */
    private static array $balanceMemo = [];

    protected $guarded = false;

    public function entity()
    {
        return $this->belongsTo(Entity::class);
    }

    protected $casts = [
        'option' => 'array',
        'category_attributes' => 'array',
        'costing_to_intermediate' => 'decimal:4',
        'intermediate_to_stock' => 'decimal:4',
        'kg_per_stock_unit' => 'decimal:8',
    ];

    public function getWarehouseAttribute()
    {
        if (Session::get('warehouse')) {
            return Session::get('warehouse');
        }

        return null;
    }

    public function issuedItems()
    {
        if ($this->warehouse) {
            return $this->hasMany(InventoryItem::class)->whereHas('inventory', function ($inventory) {
                return $inventory->where('type', 'Issue')->where('ware_house_id', $this->warehouse)->where('status',
                    'APPROVED');
            });
        }

        return $this->hasMany(InventoryItem::class)->whereHas('inventory', function ($inventory) {
            return $inventory->where('type', 'Issue')->where('status', 'APPROVED');
        });
    }

    public function transferredInItems()
    {
        if ($this->warehouse) {
            return $this->hasMany(InventoryItem::class)->whereHas('inventory', function ($inventory) {
                return $inventory->where('type', 'Transfer')
                    ->where('transfer_in', $this->warehouse)
                    ->where('status', 'APPROVED');
            });
        }

        return $this->hasMany(InventoryItem::class)->where('id', 0);
    }

    public function transferredOutItems()
    {
        if ($this->warehouse) {
            return $this->hasMany(InventoryItem::class)->whereHas('inventory', function ($inventory) {
                return $inventory->where('type', 'Transfer')
                    ->where('transfer_out', $this->warehouse)
                    ->where('status', 'APPROVED');
            });
        }

        return $this->hasMany(InventoryItem::class)->where('id', 0);
    }

    public function receivedItems()
    {
        if ($this->warehouse) {
            return $this->hasMany(InventoryItem::class)->whereHas('inventory', function ($inventory) {
                return $inventory->where('type', 'Receive')->where('ware_house_id', $this->warehouse)->where('status',
                    'APPROVED');
            });
        }

        return $this->hasMany(InventoryItem::class)->whereHas('inventory', function ($inventory) {
            return $inventory->where('type', 'Receive')->where('status', 'APPROVED');
        });
    }

    public function adjustedItems()
    {
        if ($this->warehouse) {
            return $this->hasMany(InventoryItem::class)->whereHas('inventory', function ($inventory) {
                return $inventory->where('type', 'Adjustment')->where('ware_house_id',
                    $this->warehouse)->where('status', 'APPROVED');
            });
        }

        return $this->hasMany(InventoryItem::class)->whereHas('inventory', function ($inventory) {
            return $inventory->where('type', 'Adjustment')->where('status', 'APPROVED');
        });
    }

    public function billItems()
    {
        if ($this->warehouse) {
            return $this->hasMany(BillItem::class)->whereHas('bill', function ($bill) {
                return $bill->where('ware_house_id', $this->warehouse);
            });
        }

        return $this->hasMany(BillItem::class);
    }

    public function workorder_items()
    {
        return $this->hasMany(WorkOrderItem::class);
    }

    public function category()
    {
        return $this->belongsTo(Category::class, 'category_id');
    }

    public function stockBalance()
    {
        if ($this->type == 'Service') {
            return 0;
        }

        $warehouse = $this->warehouse;
        $memoKey = $this->memoKey($this->id, $warehouse);

        if (array_key_exists($memoKey, self::$balanceMemo)) {
            return self::$balanceMemo[$memoKey];
        }

        $balances = self::stockBalancesFor([$this->id], $warehouse);
        self::$balanceMemo[$memoKey] = $balances[$this->id] ?? 0;

        return self::$balanceMemo[$memoKey];
    }

    /**
     * Batch warehouse-aware stock balances keyed by product id.
     *
     * @param  iterable<int>  $productIds
     * @return array<int, int>
     */
    public static function stockBalancesFor(iterable $productIds, ?int $warehouse = null): array
    {
        $ids = collect($productIds)->filter()->unique()->values()->all();
        if ($ids === []) {
            return [];
        }

        $types = static::query()->whereIn('id', $ids)->pluck('type', 'id');
        $balances = [];

        foreach ($ids as $id) {
            if (($types[$id] ?? null) === 'Service') {
                $balances[$id] = 0;
            }
        }

        $stockIds = collect($ids)->reject(fn ($id) => ($types[$id] ?? null) === 'Service')->values()->all();
        if ($stockIds === []) {
            return $balances;
        }

        $warehouse = $warehouse ?? Session::get('warehouse');

        $positiveTypes = ['Adjustment', 'Receive'];
        $negativeTypes = ['Issue'];

        foreach ($positiveTypes as $type) {
            foreach (self::sumInventoryQuantities($stockIds, $warehouse, $type) as $productId => $qty) {
                $balances[$productId] = ($balances[$productId] ?? 0) + $qty;
            }
        }

        foreach ($negativeTypes as $type) {
            foreach (self::sumInventoryQuantities($stockIds, $warehouse, $type) as $productId => $qty) {
                $balances[$productId] = ($balances[$productId] ?? 0) - $qty;
            }
        }

        if ($warehouse) {
            foreach (self::sumTransferQuantities($stockIds, $warehouse, 'transfer_in') as $productId => $qty) {
                $balances[$productId] = ($balances[$productId] ?? 0) + $qty;
            }
            foreach (self::sumTransferQuantities($stockIds, $warehouse, 'transfer_out') as $productId => $qty) {
                $balances[$productId] = ($balances[$productId] ?? 0) - $qty;
            }
        }

        foreach (self::sumBillItemQuantities($stockIds, $warehouse) as $productId => $qty) {
            $balances[$productId] = ($balances[$productId] ?? 0) - $qty;
        }

        foreach ($stockIds as $id) {
            $balances[$id] = (int) ($balances[$id] ?? 0);
        }

        return $balances;
    }

    /**
     * @param  array<int>  $balances
     */
    public static function primeStockBalanceMemo(array $balances, ?int $warehouse = null): void
    {
        foreach ($balances as $productId => $balance) {
            self::$balanceMemo[self::memoKey((int) $productId, $warehouse)] = $balance;
        }
    }

    public static function clearStockBalanceMemo(): void
    {
        self::$balanceMemo = [];
    }

    private static function memoKey(int $productId, ?int $warehouse): string
    {
        return $productId.':'.($warehouse ?? 'all');
    }

    /**
     * @param  array<int>  $productIds
     * @return array<int, float>
     */
    private static function sumInventoryQuantities(array $productIds, ?int $warehouse, string $type): array
    {
        $query = DB::table('inventory_items')
            ->join('inventories', 'inventories.id', '=', 'inventory_items.inventory_id')
            ->whereIn('inventory_items.product_id', $productIds)
            ->where('inventories.type', $type)
            ->where('inventories.status', 'APPROVED');

        if ($warehouse !== null) {
            $query->where('inventories.ware_house_id', $warehouse);
        }

        return $query
            ->groupBy('inventory_items.product_id')
            ->selectRaw('inventory_items.product_id as product_id, COALESCE(SUM(inventory_items.quantity), 0) as total')
            ->pluck('total', 'product_id')
            ->map(fn ($value) => (float) $value)
            ->all();
    }

    /**
     * @param  array<int>  $productIds
     * @return array<int, float>
     */
    private static function sumTransferQuantities(array $productIds, int $warehouse, string $column): array
    {
        return DB::table('inventory_items')
            ->join('inventories', 'inventories.id', '=', 'inventory_items.inventory_id')
            ->whereIn('inventory_items.product_id', $productIds)
            ->where('inventories.type', 'Transfer')
            ->where('inventories.status', 'APPROVED')
            ->where("inventories.{$column}", $warehouse)
            ->groupBy('inventory_items.product_id')
            ->selectRaw('inventory_items.product_id as product_id, COALESCE(SUM(inventory_items.quantity), 0) as total')
            ->pluck('total', 'product_id')
            ->map(fn ($value) => (float) $value)
            ->all();
    }

    /**
     * @param  array<int>  $productIds
     * @return array<int, float>
     */
    private static function sumBillItemQuantities(array $productIds, ?int $warehouse): array
    {
        $query = DB::table('bill_items')
            ->join('bills', 'bills.id', '=', 'bill_items.bill_id')
            ->whereIn('bill_items.product_id', $productIds);

        if ($warehouse !== null) {
            $query->where('bills.ware_house_id', $warehouse);
        }

        return $query
            ->groupBy('bill_items.product_id')
            ->selectRaw('bill_items.product_id as product_id, COALESCE(SUM(bill_items.quantity), 0) as total')
            ->pluck('total', 'product_id')
            ->map(fn ($value) => (float) $value)
            ->all();
    }

    /**
     * Legacy relation-summation balance used for parity tests only.
     */
    public function stockBalanceFromRelations(): int
    {
        if ($this->type == 'Service') {
            return 0;
        }
        if ($this->warehouse) {
            return (int) (($this->adjustedItems->sum('quantity') + $this->receivedItems->sum('quantity')
                    + $this->transferredInItems()->sum('quantity'))
                - ($this->billItems->sum('quantity') + $this->issuedItems->sum('quantity')
                    + $this->transferredOutItems()->sum('quantity')));
        }

        return (int) ($this->adjustedItems->sum('quantity') + $this->receivedItems->sum('quantity')
            - $this->billItems->sum('quantity')
            - $this->issuedItems->sum('quantity'));
    }

    public function avarageCost()
    {

        return $this->price ?? 0;
        $product_value = $this->receivedItems->sum(function ($item) {
            return $item->quantity * $item->cost;
        }) - $this->issuedItems->sum(function ($item) {
            return $item->quantity * $item->cost;
        });
        if ($this->stockBalance() > 0) {
            return $product_value / $this->stockBalance();
        }

        return 0;

    }

    public function unit()
    {
        return $this->belongsTo(Unit::class);
    }

    public function productImages()
    {
        return $this->hasMany(ProductImage::class);
    }

    public function options()
    {
        return $this->hasMany(Option::class);
    }

    public function lowStock()
    {
        return $this->stockBalance() <= $this->reorder;
    }

    /**
     * Get full product name including GSM and Size if available.
     */
    public function fullName(): string
    {
        $parts = [$this->name];
        if ($this->gsm) {
            $parts[] = $this->gsm.'gsm';
        }
        if ($this->size) {
            $parts[] = $this->size;
        }

        return implode(' ', $parts);
    }

    /**
     * Get formatted stock label for display in search dropdowns
     * Returns text and CSS class for badge styling
     */
    public function getStockLabelAttribute()
    {
        if ($this->type === 'Service') {
            return ['text' => 'Service', 'color' => 'secondary'];
        }

        $stock = $this->stockBalance();

        return [
            'text' => number_format($stock).' '.($this->unit->name ?? 'pcs'),
            'color' => $this->lowStock() ? 'danger' : 'success',
        ];
    }

    public function inventory_entries()
    {
        return $this->hasMany(InventoryItem::class)->orderBy('id', 'DESC')->limit(1000);
    }

    /**
     * Calculate total stock units from costing quantity.
     * Example: 15 pallets × 20 reams/pallet × 500 sheets/ream = 150,000 sheets
     */
    public function calculateStockUnits(float $costingQuantity): float
    {
        $intermediate = $this->costing_to_intermediate ?? 1;
        $stock = $this->intermediate_to_stock ?? 1;

        return $costingQuantity * $intermediate * $stock;
    }

    /**
     * Calculate intermediate units from costing quantity.
     * Example: 15 pallets × 20 reams/pallet = 300 reams
     */
    public function calculateIntermediateUnits(float $costingQuantity): float
    {
        return $costingQuantity * ($this->costing_to_intermediate ?? 1);
    }

    /**
     * Get the category's costing unit label for this product.
     */
    public function getCostingUnitLabel(): string
    {
        return $this->category?->getCostingUnit()['label'] ?? 'Unit';
    }

    /**
     * Get the category's intermediate unit label for this product.
     */
    public function getIntermediateUnitLabel(): string
    {
        return $this->category?->getIntermediateUnit()['label'] ?? 'Unit';
    }

    /**
     * Get the category's stock unit label for this product.
     */
    public function getStockUnitLabel(): string
    {
        return $this->category?->getStockUnit()['label'] ?? $this->unit?->name ?? 'Unit';
    }

    /**
     * Check if product has conversion factors configured.
     */
    public function hasConversionFactors(): bool
    {
        return $this->costing_to_intermediate !== null || $this->intermediate_to_stock !== null;
    }

    protected static function booted()
    {
        static::creating(function ($model) {
            static::assignEntityIdIfMissing($model);
        });
    }
}
