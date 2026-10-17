<?php

namespace Tests\Feature\Demo;

use App\Http\Controllers\WorkOrderController;
use App\Http\Livewire\BillComponent;
use App\Http\Livewire\CustomerComponent;
use App\Http\Livewire\ProfileComponent;
use App\Http\Livewire\WorkOrderComponent;
use App\Models\Bill;
use App\Models\ControlNumber;
use App\Models\Customer;
use App\Models\Department;
use App\Models\Entity;
use App\Models\Specification;
use App\Models\User;
use App\Models\WorkOrder;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Livewire\Livewire;
use Spatie\Permission\Models\Permission;
use Tests\TestCase;

class KeptPageWritesTest extends TestCase
{
    use RefreshDatabase;

    protected User $financeUser;
    protected User $productionUser;

    protected function setUp(): void
    {
        parent::setUp();

        Entity::factory()->create(['id' => 1]);

        foreach (['view customer', 'view invoice', 'view job', 'add job', 'delete job'] as $name) {
            Permission::firstOrCreate(['name' => $name, 'guard_name' => 'web'], ['group' => 'demo']);
        }

        $this->financeUser = User::factory()->create(['role' => 'user', 'entity_id' => 1]);
        $this->financeUser->givePermissionTo(['view customer', 'view invoice', 'view job']);

        $this->productionUser = User::factory()->create(['role' => 'user', 'entity_id' => 1]);
        $this->productionUser->givePermissionTo(['view job']);
    }

    public function test_finance_cannot_store_a_customer(): void
    {
        $this->actingAs($this->financeUser);
        Livewire::test(CustomerComponent::class)
            ->call('store')
            ->assertForbidden();
    }

    public function test_finance_cannot_destroy_a_customer(): void
    {
        $this->actingAs($this->financeUser);
        $customer = Customer::factory()->create();
        Livewire::test(CustomerComponent::class)
            ->set('delete_confirm', $customer)
            ->call('destroy')
            ->assertForbidden();
        $this->assertModelExists($customer);
    }

    public function test_finance_cannot_delete_a_bill(): void
    {
        $this->actingAs($this->financeUser);
        $controlNumber = ControlNumber::withoutEvents(fn () => ControlNumber::create([
            'entity_id' => 1,
            'control_number' => 'CN-'.uniqid(),
        ]));
        $bill = Bill::factory()->create([
            'type' => 'Invoice',
            'entity_id' => 1,
            'control_number_id' => $controlNumber->id,
        ]);

        Livewire::test(BillComponent::class, ['type' => 'Invoice'])
            ->set('delete_confirm', $bill)
            ->call('destroy')
            ->assertForbidden();

        $this->assertModelExists($bill);
    }

    public function test_cannot_store_a_work_order(): void
    {
        $this->actingAs($this->productionUser);
        Livewire::test(WorkOrderComponent::class)
            ->call('store')
            ->assertForbidden();
    }

    public function test_cannot_copy_job(): void
    {
        $this->actingAs($this->productionUser);
        Livewire::test(WorkOrderComponent::class)
            ->call('copyJob')
            ->assertForbidden();
    }

    public function test_cannot_destroy_a_work_order(): void
    {
        $this->actingAs($this->productionUser);
        $workOrder = WorkOrder::factory()->create([
            'customer_id' => Customer::factory(),
            'department_id' => Department::factory(),
            'specification_id' => Specification::factory(),
        ]);

        Livewire::test(WorkOrderComponent::class)
            ->set('delete_confirm', $workOrder)
            ->call('destroy')
            ->assertForbidden();

        $this->assertModelExists($workOrder);
    }

    public function test_cannot_change_password(): void
    {
        $this->actingAs($this->financeUser);
        Livewire::test(ProfileComponent::class)
            ->call('changePassword')
            ->assertForbidden();
    }

    public function test_work_order_controller_has_no_create_method(): void
    {
        $this->assertFalse(method_exists(WorkOrderController::class, 'create'));
    }
}
