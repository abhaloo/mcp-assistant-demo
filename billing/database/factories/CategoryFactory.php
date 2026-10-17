<?php

namespace Database\Factories;

use App\Models\Category;
use Illuminate\Database\Eloquent\Factories\Factory;

class CategoryFactory extends Factory
{
    protected $model = Category::class;

    public function definition(): array
    {
        return [
            'name' => fake()->words(2, true),
            'entity_id' => fn () => FactoryEntity::id(),
        ];
    }

    /**
     * Configure category with 3-level unit hierarchy (Pallet → Ream → Sheet).
     */
    public function paperHierarchy(): static
    {
        return $this->state(fn (array $attributes) => [
            'name' => 'BOND PAPERS',
            'unit_hierarchy' => [
                'count_levels' => [
                    ['key' => 'pallet', 'label' => 'Pallet', 'is_stock_unit' => false],
                    ['key' => 'ream', 'label' => 'Ream', 'is_stock_unit' => false],
                    ['key' => 'sheet', 'label' => 'Sheet', 'is_stock_unit' => true],
                ],
                'attributes' => [
                    ['key' => 'gsm', 'label' => 'GSM', 'type' => 'number'],
                    ['key' => 'size', 'label' => 'Size', 'type' => 'text'],
                ],
            ],
        ]);
    }

    /**
     * Configure category with 2-level unit hierarchy (Tin → Kg).
     */
    public function inkHierarchy(): static
    {
        return $this->state(fn (array $attributes) => [
            'name' => 'INKS',
            'unit_hierarchy' => [
                'count_levels' => [
                    ['key' => 'tin', 'label' => 'Tin', 'is_stock_unit' => false],
                    ['key' => 'kg', 'label' => 'Kg', 'is_stock_unit' => true],
                ],
            ],
        ]);
    }

    /**
     * Configure category without unit hierarchy.
     */
    public function noHierarchy(): static
    {
        return $this->state(fn (array $attributes) => [
            'name' => 'Services',
            'unit_hierarchy' => null,
        ]);
    }
}
