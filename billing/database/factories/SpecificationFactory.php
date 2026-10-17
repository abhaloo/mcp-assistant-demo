<?php

namespace Database\Factories;

use App\Models\Specification;
use Illuminate\Database\Eloquent\Factories\Factory;

class SpecificationFactory extends Factory
{
    protected $model = Specification::class;

    public function definition()
    {
        return [
            'name' => $this->faker->words(3, true),
            'entity_id' => fn () => FactoryEntity::id(),
        ];
    }
}
