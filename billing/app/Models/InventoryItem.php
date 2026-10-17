<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;

class InventoryItem extends Model
{
    use HasFactory;

    protected $guarded = false;

    protected $casts = [
        'cost_breakdown' => 'array',
        'unit_price_foreign' => 'decimal:4',
        'unit_cost_tzs' => 'decimal:4',
        'costing_quantity' => 'decimal:4',
        'costing_to_intermediate' => 'decimal:4',
        'intermediate_to_stock' => 'decimal:4',
        'intermediate_quantity' => 'decimal:4',
        'cost_per_costing_unit' => 'decimal:4',
        'cost_per_intermediate' => 'decimal:4',
    ];

    public function product()
    {
        return $this->belongsTo(Product::class);
    }

    public function account()
    {
        return $this->belongsTo(Account::class, 'product_id');
    }

    public function inventory()
    {
        return $this->belongsTo(Inventory::class);
    }

    /**
     * Get the landed cost per unit (alias for unit_cost_tzs or cost).
     */
    public function landedCost(): float
    {
        return $this->unit_cost_tzs ?? $this->cost ?? 0;
    }

    /**
     * Get total line value (quantity * landed cost).
     */
    public function lineTotal(): float
    {
        return ($this->quantity ?? 0) * $this->landedCost();
    }

    /**
     * Check if this item has foreign currency pricing.
     */
    public function hasForeignPricing(): bool
    {
        return ($this->unit_price_foreign ?? 0) > 0;
    }

    /**
     * Get formatted cost breakdown for display.
     */
    public function getFormattedCostBreakdown(): array
    {
        $product = $this->product;
        $category = $product?->category;

        $breakdown = [];

        if ($this->cost_per_costing_unit && $category?->getCostingUnit()) {
            $breakdown[] = [
                'label' => 'Cost per ' . $category->getCostingUnit()['label'],
                'value' => $this->cost_per_costing_unit,
            ];
        }

        if ($this->cost_per_intermediate && $category?->getIntermediateUnit()) {
            $breakdown[] = [
                'label' => 'Cost per ' . $category->getIntermediateUnit()['label'],
                'value' => $this->cost_per_intermediate,
            ];
        }

        $breakdown[] = [
            'label' => 'Cost per ' . ($category?->getStockUnit()['label'] ?? $product?->unit?->name ?? 'Unit'),
            'value' => $this->unit_cost_tzs ?? $this->cost,
        ];

        return $breakdown;
    }
}
