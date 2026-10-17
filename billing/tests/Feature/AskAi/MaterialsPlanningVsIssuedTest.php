<?php

declare(strict_types=1);

namespace Tests\Feature\AskAi;

use App\Models\Department;
use App\Models\Entity;
use App\Models\Inventory;
use App\Models\InventoryItem;
use App\Models\Product;
use App\Models\User;
use App\Models\WorkOrder;
use App\Models\WorkOrderItem;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Tests\TestCase;

class MaterialsPlanningVsIssuedTest extends TestCase
{
    use RefreshDatabase;

    protected Entity $entity;

    protected Department $department;

    protected User $user;

    protected function setUp(): void
    {
        parent::setUp();

        $this->entity = Entity::factory()->create(['id' => 1]);
        $this->department = Department::factory()->create([
            'name' => 'Printing',
        ]);
        $this->user = User::factory()->create([
            'role' => 'admin',
            'entity_id' => $this->entity->id,
        ]);
    }

    /** @test */
    public function planning_grid_rows_are_distinct_from_issued_inventory_rows(): void
    {
        $job = WorkOrder::factory()->create([
            'entity_id' => $this->entity->id,
            'user_id' => $this->user->id,
            'department_id' => $this->department->id,
        ]);

        $productPlanning = Product::factory()->create([
            'name' => 'Planning Paper 100gsm',
        ]);
        $productIssued = Product::factory()->create([
            'name' => 'Actual Issued Paper 120gsm',
        ]);

        // Planning grid row on work_order_items
        $planningItem = WorkOrderItem::create([
            'work_order_id' => $job->id,
            'product_id' => $productPlanning->id,
            'quantity' => 500,
        ]);

        // Issued material row via inventories -> inventory_items
        $inventory = Inventory::factory()->create([
            'entity_id' => $this->entity->id,
            'work_order_id' => $job->id,
            'type' => 'issue',
        ]);
        $issuedItem = InventoryItem::create([
            'inventory_id' => $inventory->id,
            'product_id' => $productIssued->id,
            'quantity' => 450,
        ]);

        // Verify relationships and distinct sources
        $jobPlanningItems = WorkOrderItem::where('work_order_id', $job->id)->get();
        $this->assertCount(1, $jobPlanningItems);
        $this->assertSame($productPlanning->id, $jobPlanningItems->first()->product_id);
        $this->assertSame(500, (int) $jobPlanningItems->first()->quantity);

        $jobIssuedItems = InventoryItem::whereHas('inventory', function ($q) use ($job) {
            $q->where('work_order_id', $job->id);
        })->get();
        $this->assertCount(1, $jobIssuedItems);
        $this->assertSame($productIssued->id, $jobIssuedItems->first()->product_id);
        $this->assertSame(450, (int) $jobIssuedItems->first()->quantity);
    }
}
