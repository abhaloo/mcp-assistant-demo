<?php

namespace Database\Factories;

use App\Models\WareHouse;
use Illuminate\Database\Eloquent\Factories\Factory;

class WareHouseFactory extends Factory
{
    protected $model = WareHouse::class;

    public function definition(): array
    {
        return [
            'name' => fake()->words(2, true) . ' Warehouse',
            'department_id' => null,
            'departments' => '[]',
        ];
    }
}
