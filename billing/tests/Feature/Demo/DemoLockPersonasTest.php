<?php

namespace Tests\Feature\Demo;

use App\Models\Department;
use App\Models\Entity;
use App\Models\User;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Hash;
use Spatie\Permission\Models\Permission;
use Spatie\Permission\Models\Role;
use Tests\TestCase;

class DemoLockPersonasTest extends TestCase
{
    use RefreshDatabase;

    protected function setUp(): void
    {
        parent::setUp();

        Entity::factory()->create(['id' => 1]);
        Department::factory()->create(['id' => 3]);

        foreach (['view customer', 'view invoice', 'view job', 'add invoice', 'delete job', 'view supplier'] as $name) {
            Permission::firstOrCreate(['name' => $name, 'guard_name' => 'web'], ['group' => 'demo']);
        }
    }

    public function test_command_fails_if_users_are_missing(): void
    {
        $this->artisan('demo:lock-personas')
            ->assertExitCode(1)
            ->expectsOutputToContain('demo_finance');
    }

    public function test_command_locks_personas_with_exact_permissions_and_budget(): void
    {
        $role = Role::firstOrCreate(['name' => 'Manager', 'guard_name' => 'web']);
        $role->givePermissionTo(['add invoice', 'view supplier']);

        $finance = User::factory()->create([
            'username' => 'demo_finance',
            'role' => 'user',
            'entity_id' => 1,
            'department_id' => 3,
            'password' => Hash::make('old-password'),
        ]);
        $finance->assignRole($role);
        $finance->givePermissionTo(['add invoice', 'delete job']);

        $production = User::factory()->create([
            'username' => 'demo_production',
            'role' => 'user',
            'entity_id' => 1,
            'department_id' => 3,
            'password' => Hash::make('old-password'),
        ]);
        $production->assignRole($role);
        $production->givePermissionTo(['delete job']);

        $this->artisan('demo:lock-personas')->assertExitCode(0);

        $finance->refresh();
        $this->assertNull($finance->department_id);
        $this->assertTrue($finance->roles->isEmpty());
        $this->assertFalse(Hash::check('old-password', $finance->password));
        $this->assertSame(
            ['view customer', 'view invoice', 'view job'],
            $finance->getAllPermissions()->pluck('name')->sort()->values()->all()
        );
        $this->assertEquals(5.00, (float) $finance->entity->fresh()->ask_budget_usd);

        $production->refresh();
        $this->assertNull($production->department_id);
        $this->assertTrue($production->roles->isEmpty());
        $this->assertFalse(Hash::check('old-password', $production->password));
        $this->assertSame(
            ['view job'],
            $production->getAllPermissions()->pluck('name')->sort()->values()->all()
        );
        $this->assertEquals(5.00, (float) $production->entity->fresh()->ask_budget_usd);
    }
}
