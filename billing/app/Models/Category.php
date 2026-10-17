<?php

namespace App\Models;

use App\Traits\EntityTrait;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;

class Category extends Model
{
    use EntityTrait;
    use HasFactory;

    public function entity()
    {
        return $this->belongsTo(Entity::class);
    }

    /**
     * Get products in this category.
     */
    public function products()
    {
        return $this->hasMany(Product::class, 'category_id');
    }

    protected $fillable = ['name', 'vendor_id', 'unit_hierarchy'];

    protected $casts = [
        'unit_hierarchy' => 'array',
    ];

    /**
     * Get the count levels from unit hierarchy.
     */
    public function getCountLevels(): array
    {
        return $this->unit_hierarchy['count_levels'] ?? [];
    }

    /**
     * Get the category attributes configuration.
     */
    public function getCategoryAttributes(): array
    {
        return $this->unit_hierarchy['attributes'] ?? [];
    }

    /**
     * Get the costing unit (first level).
     */
    public function getCostingUnit(): ?array
    {
        $levels = $this->getCountLevels();

        return $levels[0] ?? null;
    }

    /**
     * Get the intermediate unit (second level, only if 3+ levels exist).
     */
    public function getIntermediateUnit(): ?array
    {
        $levels = $this->getCountLevels();

        return (count($levels) >= 3) ? ($levels[1] ?? null) : null;
    }

    /**
     * Get the stock unit (last level with is_stock_unit=true).
     */
    public function getStockUnit(): ?array
    {
        $levels = $this->getCountLevels();
        foreach (array_reverse($levels) as $level) {
            if ($level['is_stock_unit'] ?? false) {
                return $level;
            }
        }

        return end($levels) ?: null;
    }

    /**
     * Check if category has intermediate unit level.
     */
    public function hasIntermediateUnit(): bool
    {
        return count($this->getCountLevels()) >= 3;
    }

    /**
     * Check if category has unit hierarchy configured.
     */
    public function hasUnitHierarchy(): bool
    {
        return ! empty($this->unit_hierarchy) && ! empty($this->getCountLevels());
    }

    /**
     * Get attribute configuration by key.
     */
    public function getAttributeConfig(string $key): ?array
    {
        foreach ($this->getCategoryAttributes() as $attr) {
            if ($attr['key'] === $key) {
                return $attr;
            }
        }

        return null;
    }

    protected static function booted()
    {
        static::creating(function ($model) {
            static::assignEntityIdIfMissing($model);
        });
    }
}
