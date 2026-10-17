<?php

namespace Database\Factories;

use App\Models\Tax;
use Illuminate\Database\Eloquent\Factories\Factory;

class TaxFactory extends Factory
{
    protected $model = Tax::class;

    public function definition(): array
    {
        return [
            'name' => 'VAT',
            'rate' => 18,
            'type' => 'Sales Tax',
            'account_id' => 1,
        ];
    }

    public function standard(): static
    {
        return $this->state(fn (array $attributes) => [
            'name' => 'Standard VAT',
            'rate' => 18,
        ]);
    }

    public function zero(): static
    {
        return $this->state(fn (array $attributes) => [
            'name' => 'Zero Rate',
            'rate' => 0,
        ]);
    }
}
