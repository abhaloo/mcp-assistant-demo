<?php

namespace Database\Factories;

use App\Models\Customer;
use App\Models\CustomerOrder;
use App\Models\Department;
use App\Models\User;
use App\Models\WorkOrder;
use Illuminate\Database\Eloquent\Factories\Factory;

class WorkOrderFactory extends Factory
{
    protected $model = WorkOrder::class;

    public function definition(): array
    {
        return [
            'customer_id' => Customer::factory(),
            'user_id' => User::factory(),
            'title' => fake()->sentence(3),
            'description' => fake()->paragraph(),
            'status' => 'CREATED',
            'ordered_qty' => fake()->numberBetween(1, 100),
            'entity_id' => fn () => FactoryEntity::id(),
        ];
    }

    public function withDepartment(?Department $department = null): static
    {
        return $this->state(fn (array $attributes) => [
            'department_id' => $department?->id ?? Department::factory(),
        ]);
    }

    public function inProgress(): static
    {
        return $this->state(fn (array $attributes) => [
            'status' => 'IN PROGRESS',
        ]);
    }

    public function finished(): static
    {
        return $this->state(fn (array $attributes) => [
            'status' => 'FINISHED',
        ]);
    }

    public function withOrder(?CustomerOrder $order = null): static
    {
        return $this->state(fn (array $attributes) => [
            'customer_order_id' => $order?->id ?? CustomerOrder::factory(),
        ]);
    }
}
