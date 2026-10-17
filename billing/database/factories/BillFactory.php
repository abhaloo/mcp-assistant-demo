<?php

namespace Database\Factories;

use App\Models\Bill;
use App\Models\Currency;
use App\Models\Customer;
use Illuminate\Database\Eloquent\Factories\Factory;

class BillFactory extends Factory
{
    protected $model = Bill::class;

    public function definition(): array
    {
        return [
            'type' => 'Invoice',
            'customer_id' => Customer::factory(),
            'currency_id' => Currency::factory(),
            'control_number_id' => 0,
            'tax' => 0,
            'invoice_number' => fake()->unique()->numberBetween(1000, 9999),
            'description' => fake()->sentence(),
            'payable' => 'No',
            'entity_id' => fn () => FactoryEntity::id(),
        ];
    }

    public function invoice(): static
    {
        return $this->state(fn (array $attributes) => [
            'type' => 'Invoice',
        ]);
    }

    public function quotation(): static
    {
        return $this->state(fn (array $attributes) => [
            'type' => 'Quotation',
        ]);
    }

    public function payableQuotation(): static
    {
        return $this->state(fn (array $attributes) => [
            'type' => 'Payable Quotation',
            'payable' => 'Yes',
        ]);
    }
}
