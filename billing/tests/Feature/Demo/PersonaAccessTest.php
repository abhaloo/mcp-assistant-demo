<?php

namespace Tests\Feature\Demo;

use App\Models\Entity;
use App\Models\User;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Hash;
use Illuminate\Support\Str;
use Spatie\Permission\Models\Permission;
use Tests\TestCase;

class PersonaAccessTest extends TestCase
{
    use RefreshDatabase;

    protected User $finance;
    protected User $production;

    protected function setUp(): void
    {
        parent::setUp();

        Entity::factory()->create(['id' => 1]);

        foreach (['view customer', 'view invoice', 'view job'] as $name) {
            Permission::firstOrCreate(['name' => $name, 'guard_name' => 'web'], ['group' => 'demo']);
        }

        $this->seedDemoPersonas();
    }

    protected function seedDemoPersonas(): void
    {
        $this->finance = User::factory()->create([
            'username' => 'demo_finance',
            'role' => 'user',
            'entity_id' => 1,
            'department_id' => null,
            'password' => Hash::make(Str::random(40)),
        ]);
        $this->finance->syncRoles([]);
        $this->finance->syncPermissions(['view job', 'view invoice', 'view customer']);

        $this->production = User::factory()->create([
            'username' => 'demo_production',
            'role' => 'user',
            'entity_id' => 1,
            'department_id' => null,
            'password' => Hash::make(Str::random(40)),
        ]);
        $this->production->syncRoles([]);
        $this->production->syncPermissions(['view job']);
    }

    public function test_warehouse_button_lands_on_jobs_and_cannot_open_invoices(): void
    {
        $this->post('/demo/enter/warehouse')->assertRedirect(route('work-order.index'));
        $this->get('/jobs')->assertOk();
        $this->get('/bills')->assertForbidden();
        $this->get('/customers')->assertForbidden();
    }

    public function test_finance_button_lands_on_jobs_and_can_open_invoices_and_customers(): void
    {
        $this->post('/demo/enter/finance')->assertRedirect(route('work-order.index'));
        $this->get('/jobs')->assertOk();
        $this->get('/bills')->assertOk();
        $this->get('/customers')->assertOk();
    }

    public function test_finance_invoice_index_rejects_quotation_type(): void
    {
        $this->post('/demo/enter/finance');
        $this->get('/bills?type=Quotation')->assertNotFound();
        $this->get('/bills?type=Invoice')->assertOk();
    }

    public function test_password_login_is_gone(): void
    {
        $this->post('/login', ['username' => 'demo_finance', 'password' => 'password'])
            ->assertNotFound();
        $this->post('/register', [])->assertNotFound();
        $this->actingAs($this->finance);
        $this->get('/user/setting')->assertNotFound();
    }

    public function test_unknown_persona_returns_404(): void
    {
        $this->post('/demo/enter/admin')->assertNotFound();
    }

    public function test_missing_persona_user_returns_503(): void
    {
        $this->finance->delete();
        $this->post('/demo/enter/finance')->assertStatus(503);
    }

    public function test_guest_landing_page(): void
    {
        $response = $this->get('/');
        $response->assertOk();
        $response->assertSee('Demo data is anonymised.');
        $response->assertSee('Try as Finance');
        $response->assertSee('Try as Warehouse');
        $response->assertDontSee('type="password"', false);
    }

    public function test_authenticated_user_on_home_redirects_to_jobs(): void
    {
        $this->actingAs($this->finance);
        $this->get('/')->assertRedirect(route('work-order.index'));
    }
}
