<?php

namespace Database\Factories;

use App\Models\User;
use App\Models\WorkOrder;
use App\Models\WorkOrderAttachment;
use Illuminate\Database\Eloquent\Factories\Factory;

class WorkOrderAttachmentFactory extends Factory
{
    protected $model = WorkOrderAttachment::class;

    public function definition(): array
    {
        return [
            'work_order_id' => WorkOrder::factory(),
            'user_id' => User::factory(),
            'path' => 'attachments/'.fake()->uuid().'.pdf',
            'description' => fake()->sentence(3),
            'extension' => 'pdf',
            'size' => 1024,
        ];
    }
}
