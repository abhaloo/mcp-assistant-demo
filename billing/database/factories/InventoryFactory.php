<?php

namespace Database\Factories;

use App\Models\Inventory;
use Illuminate\Database\Eloquent\Factories\Factory;

class InventoryFactory extends Factory
{
    protected $model = Inventory::class;

    public function definition(): array
    {
        return [
            'type' => 'Receive',
            'ware_house_id' => 1,
            'entity_id' => fn () => FactoryEntity::id(),
            'inventory_date' => now(),
            'status' => 'APPROVED',
        ];
    }

    /**
     * Configure as receive entry.
     */
    public function receive(): static
    {
        return $this->state(fn (array $attributes) => [
            'type' => 'Receive',
        ]);
    }

    /**
     * Configure as issue entry.
     */
    public function issue(): static
    {
        return $this->state(fn (array $attributes) => [
            'type' => 'Issue',
        ]);
    }

    /**
     * Configure as goods costing bill.
     */
    public function goodsCosting(): static
    {
        return $this->state(fn (array $attributes) => [
            'type' => 'Supplier Bill',
            'is_goods_costing' => true,
        ]);
    }
}
