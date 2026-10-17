<?php

namespace Tests\Feature;

use App\Models\Bill;
use App\Models\ControlNumber;
use App\Models\Customer;
use App\Models\Department;
use App\Models\Entity;
use App\Models\Specification;
use App\Models\User;
use App\Models\WorkOrder;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Spatie\Permission\Models\Permission;
use Tests\TestCase;

class BackLinkTest extends TestCase
{
    use RefreshDatabase;

    protected User $user;

    protected function setUp(): void
    {
        parent::setUp();

        Entity::factory()->create(['id' => 1]);

        foreach (['view invoice', 'view quotation'] as $name) {
            Permission::firstOrCreate(['name' => $name, 'guard_name' => 'web'], ['group' => 'invoice']);
        }

        $this->user = User::factory()->create(['role' => 'user', 'entity_id' => 1]);
        $this->user->givePermissionTo(['view invoice', 'view quotation']);
        $this->actingAs($this->user);
    }

    protected function bill(string $type = 'Invoice'): Bill
    {
        $controlNumber = ControlNumber::withoutEvents(fn () => ControlNumber::create([
            'entity_id' => 1,
            'control_number' => 'CN-'.uniqid(),
        ]));

        return Bill::factory()->create(['type' => $type, 'control_number_id' => $controlNumber->id]);
    }

    protected function assertBackLinkTo(string $url, $response): void
    {
        $response->assertOk()->assertSee('href="'.e($url).'" data-back-link', false);
    }

    /** @test */
    public function invoice_opened_from_jobs_list_goes_back_to_jobs_list_with_its_filters()
    {
        $jobs = route('work-order.index', ['filter_status' => 'CREATED']);

        $response = $this->withHeader('referer', $jobs)->get(route('bill.show', $this->bill()));

        $this->assertBackLinkTo($jobs, $response);
    }

    /** @test */
    public function invoice_opened_directly_falls_back_to_the_invoice_list()
    {
        $response = $this->get(route('bill.show', $this->bill()));

        $this->assertBackLinkTo(route('bill.index', ['type' => 'Invoice']), $response);
    }

    /** @test */
    public function quotation_opened_directly_falls_back_to_the_quotation_list()
    {
        $response = $this->get(route('bill.show', $this->bill('Quotation')));

        $response->assertForbidden();
    }

    /** @test */
    public function external_referrer_is_ignored()
    {
        $response = $this->withHeader('referer', 'https://evil.example.com/jobs')
            ->get(route('bill.show', $this->bill()));

        $this->assertBackLinkTo(route('bill.index', ['type' => 'Invoice']), $response);
    }

    /** @test */
    public function referrer_of_the_same_page_is_ignored()
    {
        $bill = $this->bill();

        $response = $this->withHeader('referer', route('bill.show', $bill))->get(route('bill.show', $bill));

        $this->assertBackLinkTo(route('bill.index', ['type' => 'Invoice']), $response);
    }

    /** @test */
    public function login_page_referrer_is_ignored()
    {
        $response = $this->withHeader('referer', route('login'))->get(route('bill.show', $this->bill()));

        $this->assertBackLinkTo(route('bill.index', ['type' => 'Invoice']), $response);
    }

    /** @test */
    public function job_page_with_a_call_back_segment_renders_and_goes_back_to_the_referrer()
    {
        $workOrder = WorkOrder::factory()->create([
            'customer_id' => Customer::factory(),
            'department_id' => Department::factory(),
            'specification_id' => Specification::factory(),
        ]);
        $jobs = route('work-order.index');
        Permission::firstOrCreate(['name' => 'view job', 'guard_name' => 'web'], ['group' => 'job']);
        $this->user->givePermissionTo('view job');

        $response = $this->withHeader('referer', $jobs)->get('/jobs/show/'.$workOrder->id.'/1');

        $this->assertBackLinkTo($jobs, $response);
    }
}
