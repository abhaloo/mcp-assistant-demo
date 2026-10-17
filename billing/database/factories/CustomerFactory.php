<?php

namespace Database\Factories;

use App\Models\Currency;
use App\Models\Customer;
use Illuminate\Database\Eloquent\Factories\Factory;

class CustomerFactory extends Factory
{
    protected $model = Customer::class;

    public function definition(): array
    {
        return [
            'name' => fake()->company(),
            'type' => fake()->randomElement(['Individual', 'Company']),
            'phone' => fake()->phoneNumber(),
            'email' => fake()->safeEmail(),
            'address' => fake()->address(),
            'currency_id' => Currency::factory(),
            'entity_id' => fn () => FactoryEntity::id(),
        ];
    }
}
