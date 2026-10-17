<?php

declare(strict_types=1);

namespace Tests\Feature\Authorization\BusinessQuery;

use App\Authorization\BusinessQuery\Catalog\DefaultResources;
use App\Models\Bill;
use App\Models\Entity;
use App\Models\User;
use App\Policies\BillPolicy;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Gate;
use Spatie\Permission\Models\Permission;
use Tests\TestCase;

class DemoRecordRouteTest extends TestCase
{
    use RefreshDatabase;

    public function test_only_invoice_job_and_customer_have_record_routes(): void
    {
        $routes = [];
        foreach (DefaultResources::all() as $binding) {
            $routes[$binding->name] = $binding->recordRoute;
        }
        $this->assertSame('bill.show', $routes['invoice']);
        $this->assertSame('work-order.show', $routes['job']);
        $this->assertSame('customer.show', $routes['customer']);
        foreach (['quotation', 'payable_quotation', 'customer_order', 'supplier', 'inventory'] as $name) {
            $this->assertNull($routes[$name], $name);
        }
        $invoice = collect(DefaultResources::all())->firstWhere('name', 'invoice');
        $this->assertSame(['view invoice'], $invoice->linkPermissions);
    }

    public function test_user_with_view_invoice_gets_false_for_quotation_bill(): void
    {
        $entity = Entity::factory()->create(['id' => 1]);
        Permission::firstOrCreate(['name' => 'view invoice', 'guard_name' => 'web'], ['group' => 'invoice']);

        $user = User::factory()->create(['role' => 'user', 'entity_id' => $entity->id]);
        $user->givePermissionTo('view invoice');

        $quotation = Bill::factory()->quotation()->create(['entity_id' => $entity->id]);

        $policy = app(BillPolicy::class);
        $this->assertFalse($policy->view($user, $quotation));
        $this->assertFalse(Gate::forUser($user)->allows('view', $quotation));
    }

    public function test_user_with_view_invoice_can_view_invoice_bill(): void
    {
        $entity = Entity::factory()->create(['id' => 1]);
        Permission::firstOrCreate(['name' => 'view invoice', 'guard_name' => 'web'], ['group' => 'invoice']);

        $user = User::factory()->create(['role' => 'user', 'entity_id' => $entity->id]);
        $user->givePermissionTo('view invoice');

        $invoice = Bill::factory()->invoice()->create(['entity_id' => $entity->id]);

        $policy = app(BillPolicy::class);
        $this->assertTrue($policy->view($user, $invoice));
        $this->assertTrue(Gate::forUser($user)->allows('view', $invoice));
    }

    public function test_user_with_view_quotation_alone_cannot_view_invoice_bill(): void
    {
        $entity = Entity::factory()->create(['id' => 1]);
        Permission::firstOrCreate(['name' => 'view quotation', 'guard_name' => 'web'], ['group' => 'invoice']);

        $user = User::factory()->create(['role' => 'user', 'entity_id' => $entity->id]);
        $user->givePermissionTo('view quotation');

        $invoice = Bill::factory()->invoice()->create(['entity_id' => $entity->id]);

        $policy = app(BillPolicy::class);
        $this->assertFalse($policy->view($user, $invoice));
        $this->assertFalse(Gate::forUser($user)->allows('view', $invoice));
    }
}
