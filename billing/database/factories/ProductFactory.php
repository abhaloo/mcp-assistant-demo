<?php

namespace Database\Factories;

use App\Models\Category;
use App\Models\Product;
use App\Models\Unit;
use Illuminate\Database\Eloquent\Factories\Factory;

class ProductFactory extends Factory
{
    protected $model = Product::class;

    public function definition(): array
    {
        return [
            'name' => fake()->words(3, true),
            'sku' => fake()->unique()->numerify('SKU-####'),
            'type' => fake()->randomElement(['Product', 'Service']),
            'price' => fake()->randomFloat(2, 10, 1000),
            'unit_id' => Unit::factory(),
            'entity_id' => fn () => FactoryEntity::id(),
            'category_id' => function (array $attributes) {
                return Category::firstOrCreate(
                    ['name' => 'Default', 'entity_id' => $attributes['entity_id']]
                )->id;
            },
            'reorder' => fake()->numberBetween(5, 20),
            'inventory' => true,
        ];
    }

    public function product(): static
    {
        return $this->state(fn (array $attributes) => [
            'type' => 'Product',
        ]);
    }

    public function service(): static
    {
        return $this->state(fn (array $attributes) => [
            'type' => 'Service',
        ]);
    }

    public function material(): static
    {
        return $this->state(fn (array $attributes) => [
            'type' => 'Material',
        ]);
    }
}
