<?php

namespace Database\Factories;

use App\Models\Customer;
use App\Models\CustomerOrder;
use Illuminate\Database\Eloquent\Factories\Factory;

class CustomerOrderFactory extends Factory
{
    protected $model = CustomerOrder::class;

    public function definition(): array
    {
        return [
            'customer_id' => Customer::factory(),
            'order_number' => fake()->unique()->numberBetween(100, 999),
            'description' => fake()->sentence(),
            'status' => 'CREATED',
            'entity_id' => fn () => FactoryEntity::id(),
        ];
    }

    public function inProgress(): static
    {
        return $this->state(fn (array $attributes) => [
            'status' => 'IN_PROGRESS',
        ]);
    }

    public function finished(): static
    {
        return $this->state(fn (array $attributes) => [
            'status' => 'FINISHED',
        ]);
    }

    public function cancelled(): static
    {
        return $this->state(fn (array $attributes) => [
            'status' => 'CANCELLED',
        ]);
    }
}
