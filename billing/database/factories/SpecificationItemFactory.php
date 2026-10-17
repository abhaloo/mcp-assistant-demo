<?php

namespace Database\Factories;

use App\Models\Specification;
use App\Models\SpecificationItem;
use Illuminate\Database\Eloquent\Factories\Factory;

class SpecificationItemFactory extends Factory
{
    protected $model = SpecificationItem::class;

    public function definition()
    {
        return [
            'name' => $this->faker->words(2, true),
            'value' => json_encode([]),
            'type' => 'Strings',
            'columns' => 6,
            'specification_id' => Specification::factory(),
            'entity_id' => fn () => FactoryEntity::id(),
            'department_id' => null,
        ];
    }
}
