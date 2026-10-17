<?php

namespace Database\Factories;

use App\Models\Currency;
use Illuminate\Database\Eloquent\Factories\Factory;

class CurrencyFactory extends Factory
{
    protected $model = Currency::class;

    public function definition(): array
    {
        return [
            'name' => fake()->currencyCode(),
            'currency_code' => fake()->unique()->currencyCode(),
            'symbol' => fake()->randomElement(['$', '€', '£', 'USD']),
            'rate' => 1.0000,
            'entity_id' => fn () => FactoryEntity::id(),
        ];
    }
}
