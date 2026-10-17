<?php

namespace Database\Factories;

use App\Models\Unit;
use Illuminate\Database\Eloquent\Factories\Factory;

class UnitFactory extends Factory
{
    protected $model = Unit::class;

    public function definition(): array
    {
        return [
            'name' => fake()->randomElement(['Sheets', 'pcs', 'kg', 'roll', 'box', 'ltr', 'tin', 'pkt', 'bag', 'Reams']),
            'entity_id' => fn () => FactoryEntity::id(),
        ];
    }
}
