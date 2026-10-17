<?php

namespace Tests\Feature\Demo;

use App\Http\Livewire\BillComponent;
use App\Http\Livewire\CustomerComponent;
use App\Http\Livewire\CustomerSearchComponent;
use App\Http\Livewire\ProfileComponent;
use App\Http\Livewire\ShowBillComponent;
use App\Http\Livewire\WorkDataComponent;
use App\Http\Livewire\WorkOrderAttachmentComponent;
use App\Http\Livewire\WorkOrderComponent;
use App\Models\ApprovalRequest;
use App\Models\Bill;
use App\Models\BillItem;
use App\Models\ControlNumber;
use App\Models\CreditNote;
use App\Models\Customer;
use App\Models\Department;
use App\Models\Entity;
use App\Models\Specification;
use App\Models\User;
use App\Models\WorkMessage;
use App\Models\WorkOrder;
use App\Models\WorkOrderAttachment;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Http\UploadedFile;
use Illuminate\Support\Facades\Hash;
use Illuminate\Support\Facades\Storage;
use Illuminate\Support\Str;
use Livewire\Component;
use Livewire\Livewire;
use Spatie\Permission\Models\Permission;
use Tests\TestCase;

class DummyReadOnlyTestComponent extends Component
{
    public function harmlessAction(): string
    {
        return 'allowed-if-not-blocked';
    }

    public function render(): string
    {
        return '<div>Dummy</div>';
    }
}

class LivewireReadOnlyTest extends TestCase
{
    use RefreshDatabase;

    protected User $finance;
    protected User $production;
    protected Entity $entity;

    protected function setUp(): void
    {
        parent::setUp();

        $this->entity = Entity::factory()->create(['id' => 1]);

        foreach (['view customer', 'view invoice', 'view job', 'add job', 'delete job', 'approve job requests'] as $name) {
            Permission::firstOrCreate(['name' => $name, 'guard_name' => 'web'], ['group' => 'demo']);
        }

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

    protected function createInvoice(): Bill
    {
        $controlNumber = ControlNumber::withoutEvents(fn () => ControlNumber::create([
            'entity_id' => 1,
            'control_number' => 'CN-'.uniqid(),
        ]));

        $bill = Bill::factory()->create([
            'type' => 'Invoice',
            'entity_id' => 1,
            'control_number_id' => $controlNumber->id,
            'customer_id' => Customer::factory()->create(['entity_id' => 1])->id,
        ]);

        $product = \App\Models\Product::factory()->create(['entity_id' => 1]);
        $tax = \App\Models\Tax::firstOrCreate(['name' => 'VAT'], ['rate' => 0, 'type' => 'Sales Tax', 'account_id' => 1]);

        BillItem::create([
            'bill_id' => $bill->id,
            'product_id' => $product->id,
            'tax_id' => $tax->id,
            'description' => 'Test Item',
            'quantity' => 5,
            'price' => 1000,
            'tax' => 0,
            'discount' => 0,
            'quantity_returned' => 0,
        ]);

        return $bill;
    }

    protected function createJob(): WorkOrder
    {
        return WorkOrder::factory()->create([
            'entity_id' => 1,
            'customer_id' => Customer::factory()->create(['entity_id' => 1])->id,
            'department_id' => Department::factory()->create()->id,
            'specification_id' => Specification::factory()->create()->id,
            'status' => 'IN PROGRESS',
            'editing' => 'CLOSED',
        ]);
    }

    // -------------------------------------------------------------------------
    // 1. ShowBillComponent write methods
    // -------------------------------------------------------------------------

    public function test_show_bill_store_payment_aborts_403_and_creates_no_row(): void
    {
        $this->actingAs($this->finance);
        $bill = $this->createInvoice();

        Livewire::test(ShowBillComponent::class, ['bill' => $bill])
            ->set('method', 1)
            ->set('amount', 500)
            ->set('paid_date', now()->format('Y-m-d'))
            ->call('store')
            ->assertForbidden();

        $this->assertDatabaseCount('journals', 0);
    }

    public function test_show_bill_store_credit_note_aborts_403_and_creates_no_row(): void
    {
        $this->actingAs($this->finance);
        $bill = $this->createInvoice();

        Livewire::test(ShowBillComponent::class, ['bill' => $bill])
            ->set('credit_note_method', 1)
            ->call('storeCreditNote')
            ->assertForbidden();

        $this->assertDatabaseCount('credit_notes', 0);
    }

    public function test_show_bill_remove_credit_note_aborts_403_and_does_not_delete(): void
    {
        $this->actingAs($this->finance);
        $bill = $this->createInvoice();
        $creditNote = CreditNote::create([
            'bill_id' => $bill->id,
            'payment_id' => 1,
        ]);

        Livewire::test(ShowBillComponent::class, ['bill' => $bill])
            ->call('removeCreditNote', $creditNote)
            ->assertForbidden();

        $this->assertModelExists($creditNote);
    }

    // -------------------------------------------------------------------------
    // 2. WorkDataComponent write methods (tested for finance and production)
    // -------------------------------------------------------------------------

    public function test_work_data_submit_aborts_403_for_both_personas(): void
    {
        foreach ([$this->finance, $this->production] as $user) {
            $this->actingAs($user);
            $job = $this->createJob();
            $job->status = 'CREATED';
            $job->save();

            Livewire::test(WorkDataComponent::class, ['workOrder' => $job])
                ->call('submit')
                ->assertForbidden();

            $job->refresh();
            $this->assertSame('CREATED', $job->status);
        }
    }

    public function test_work_data_edit_aborts_403_for_both_personas(): void
    {
        foreach ([$this->finance, $this->production] as $user) {
            $this->actingAs($user);
            $job = $this->createJob();
            $job->editing = 'CLOSED';
            $job->save();

            Livewire::test(WorkDataComponent::class, ['workOrder' => $job])
                ->call('edit')
                ->assertForbidden();

            $job->refresh();
            $this->assertSame('CLOSED', $job->editing);
        }
    }

    public function test_work_data_store_aborts_403_for_both_personas(): void
    {
        foreach ([$this->finance, $this->production] as $user) {
            $this->actingAs($user);
            $job = $this->createJob();
            $originalTitle = $job->title;

            Livewire::test(WorkDataComponent::class, ['workOrder' => $job])
                ->set('title', 'Mutated Title')
                ->call('store')
                ->assertForbidden();

            $job->refresh();
            $this->assertSame($originalTitle, $job->title);
        }
    }

    public function test_work_data_move_aborts_403_for_both_personas(): void
    {
        foreach ([$this->finance, $this->production] as $user) {
            $this->actingAs($user);
            $job = $this->createJob();
            $origDept = $job->department_id;
            $newDept = Department::factory()->create();

            Livewire::test(WorkDataComponent::class, ['workOrder' => $job])
                ->set('department', $newDept->id)
                ->set('remark', 'Move test')
                ->call('move')
                ->assertForbidden();

            $job->refresh();
            $this->assertSame($origDept, $job->department_id);
            $this->assertDatabaseMissing('work_messages', [
                'work_order_id' => $job->id,
                'message' => 'Move test',
            ]);
        }
    }

    public function test_work_data_cancel_move_request_aborts_403_for_both_personas(): void
    {
        foreach ([$this->finance, $this->production] as $user) {
            $this->actingAs($user);
            $job = $this->createJob();

            $approval = ApprovalRequest::create([
                'entity_id' => 1,
                'approvable_type' => WorkOrder::class,
                'approvable_id' => $job->id,
                'action_type' => ApprovalRequest::TYPE_DEPARTMENT_MOVE,
                'status' => ApprovalRequest::PENDING,
                'payload' => ['previous_status' => 'IN PROGRESS'],
                'requested_by' => $user->id,
            ]);

            Livewire::test(WorkDataComponent::class, ['workOrder' => $job])
                ->call('cancelMoveRequest')
                ->assertForbidden();

            $this->assertModelExists($approval);
        }
    }

    public function test_work_data_approve_and_reject_request_abort_403(): void
    {
        foreach ([$this->finance, $this->production] as $user) {
            $this->actingAs($user);
            $job = $this->createJob();

            $approval = ApprovalRequest::create([
                'entity_id' => 1,
                'approvable_type' => WorkOrder::class,
                'approvable_id' => $job->id,
                'action_type' => ApprovalRequest::TYPE_DEPARTMENT_MOVE,
                'status' => ApprovalRequest::PENDING,
                'payload' => ['previous_status' => 'IN PROGRESS'],
                'requested_by' => $user->id,
            ]);

            Livewire::test(WorkDataComponent::class, ['workOrder' => $job])
                ->call('approveRequest')
                ->assertForbidden();

            Livewire::test(WorkDataComponent::class, ['workOrder' => $job])
                ->call('rejectRequest')
                ->assertForbidden();

            $this->assertModelExists($approval);
        }
    }

    public function test_work_data_change_customer_aborts_403_for_both_personas(): void
    {
        foreach ([$this->finance, $this->production] as $user) {
            $this->actingAs($user);
            $job = $this->createJob();
            $origCust = $job->customer_id;
            $newCust = Customer::factory()->create(['entity_id' => 1]);

            Livewire::test(WorkDataComponent::class, ['workOrder' => $job])
                ->set('new_customer', $newCust->id)
                ->call('changeCustomer')
                ->assertForbidden();

            $job->refresh();
            $this->assertSame($origCust, $job->customer_id);
        }
    }

    // -------------------------------------------------------------------------
    // 3. WorkOrderAttachmentComponent write & upload & download methods
    // -------------------------------------------------------------------------

    public function test_attachment_store_aborts_403_for_both_personas(): void
    {
        Storage::fake('private');

        foreach ([$this->finance, $this->production] as $user) {
            $this->actingAs($user);
            $job = $this->createJob();

            Livewire::test(WorkOrderAttachmentComponent::class, ['work_order_id' => $job->id])
                ->set('description', 'Test attachment')
                ->call('store')
                ->assertForbidden();

            $this->assertDatabaseCount('work_order_attachments', 0);
        }
    }

    public function test_attachment_destroy_aborts_403_for_both_personas(): void
    {
        foreach ([$this->finance, $this->production] as $user) {
            $this->actingAs($user);
            $job = $this->createJob();

            $attachment = WorkOrderAttachment::create([
                'work_order_id' => $job->id,
                'description' => 'Existing file',
                'path' => 'attachments/fake.pdf',
                'extension' => 'pdf',
                'size' => 1024,
                'user_id' => $user->id,
            ]);

            Livewire::test(WorkOrderAttachmentComponent::class, ['work_order_id' => $job->id])
                ->set('delete_confirm_id', $attachment->id)
                ->call('destroy')
                ->assertForbidden();

            $this->assertModelExists($attachment);
        }
    }

    public function test_attachment_download_allowed_only_for_authorized_view_of_existing_file(): void
    {
        Storage::fake('private');
        $job = $this->createJob();

        $disk = WorkOrderAttachment::diskName();
        Storage::disk($disk)->put('attachments/test.pdf', 'file-contents');

        $attachment = WorkOrderAttachment::create([
            'work_order_id' => $job->id,
            'description' => 'Valid file',
            'path' => 'attachments/test.pdf',
            'extension' => 'pdf',
            'size' => 13,
            'user_id' => $this->finance->id,
        ]);

        // Allowed for persona with 'view job'
        $this->actingAs($this->finance);
        $component = Livewire::test(WorkOrderAttachmentComponent::class, ['work_order_id' => $job->id]);
        $response = $component->call('download', $attachment->id);
        $this->assertNotEquals(403, $response->status());

        // Forbidden when user has no 'view job' permission
        $unauthorizedUser = User::factory()->create(['role' => 'user', 'entity_id' => 1]);
        $unauthorizedUser->syncRoles([]);
        $unauthorizedUser->syncPermissions([]);
        $this->actingAs($unauthorizedUser);

        Livewire::test(WorkOrderAttachmentComponent::class, ['work_order_id' => $job->id])
            ->call('download', $attachment->id)
            ->assertForbidden();
    }

    public function test_upload_start_and_upload_endpoint_are_forbidden(): void
    {
        $this->actingAs($this->finance);
        $job = $this->createJob();

        // Calling _startUpload on component directly
        Livewire::test(WorkOrderAttachmentComponent::class, ['work_order_id' => $job->id])
            ->call('_startUpload', 'file', [], false)
            ->assertForbidden();

        // Posting to upload endpoint
        $file = UploadedFile::fake()->create('test.pdf', 100);
        $response = $this->post('/livewire/upload-file', [
            'files' => [$file],
        ]);
        $response->assertForbidden();
    }

    // -------------------------------------------------------------------------
    // 4. WorkOrderComponent write methods
    // -------------------------------------------------------------------------

    public function test_work_order_component_copy_job_aborts_403(): void
    {
        foreach ([$this->finance, $this->production] as $user) {
            $this->actingAs($user);
            $job = $this->createJob();

            Livewire::test(WorkOrderComponent::class)
                ->call('copyJob')
                ->assertForbidden();
        }
    }

    public function test_work_order_component_store_aborts_403(): void
    {
        foreach ([$this->finance, $this->production] as $user) {
            $this->actingAs($user);

            Livewire::test(WorkOrderComponent::class)
                ->call('store')
                ->assertForbidden();
        }
    }

    public function test_work_order_component_destroy_aborts_403(): void
    {
        foreach ([$this->finance, $this->production] as $user) {
            $this->actingAs($user);
            $job = $this->createJob();

            Livewire::test(WorkOrderComponent::class)
                ->set('delete_confirm', $job)
                ->call('destroy')
                ->assertForbidden();

            $this->assertModelExists($job);
        }
    }

    public function test_work_order_component_cancel_move_request_aborts_403(): void
    {
        foreach ([$this->finance, $this->production] as $user) {
            $this->actingAs($user);
            $job = $this->createJob();

            $approval = ApprovalRequest::create([
                'entity_id' => 1,
                'approvable_type' => WorkOrder::class,
                'approvable_id' => $job->id,
                'action_type' => ApprovalRequest::TYPE_DEPARTMENT_MOVE,
                'status' => ApprovalRequest::PENDING,
                'payload' => ['previous_status' => 'IN PROGRESS'],
                'requested_by' => $user->id,
            ]);

            Livewire::test(WorkOrderComponent::class)
                ->call('cancelMoveRequest', $job->id)
                ->assertForbidden();

            $this->assertModelExists($approval);
        }
    }

    // -------------------------------------------------------------------------
    // 5. CustomerComponent write methods
    // -------------------------------------------------------------------------

    public function test_customer_component_store_aborts_403(): void
    {
        $this->actingAs($this->finance);

        Livewire::test(CustomerComponent::class)
            ->call('store')
            ->assertForbidden();
    }

    public function test_customer_component_destroy_aborts_403(): void
    {
        $this->actingAs($this->finance);
        $customer = Customer::factory()->create(['entity_id' => 1]);

        Livewire::test(CustomerComponent::class)
            ->set('delete_confirm', $customer)
            ->call('destroy')
            ->assertForbidden();

        $this->assertModelExists($customer);
    }

    // -------------------------------------------------------------------------
    // 6. ProfileComponent changePassword
    // -------------------------------------------------------------------------

    public function test_profile_component_change_password_aborts_403(): void
    {
        $this->actingAs($this->finance);

        Livewire::test(ProfileComponent::class)
            ->call('changePassword')
            ->assertForbidden();
    }

    // -------------------------------------------------------------------------
    // 7. Allow-listed read methods succeed
    // -------------------------------------------------------------------------

    public function test_allow_listed_read_interactions_still_work(): void
    {
        $this->actingAs($this->finance);
        $bill = $this->createInvoice();

        // BillComponent sorting and pagination
        Livewire::test(BillComponent::class, ['type' => 'Invoice'])
            ->call('setSortField', 'created_at')
            ->assertOk()
            ->call('setPage', 1)
            ->assertOk();

        // WorkOrderComponent density and sorting
        Livewire::test(WorkOrderComponent::class)
            ->call('setDensity', 'compact')
            ->assertOk()
            ->call('setSortField', 'created_at')
            ->assertOk();

        // CustomerSearchComponent search selection
        $customer = Customer::factory()->create(['entity_id' => 1, 'name' => 'Acme Corp']);
        Livewire::test(CustomerSearchComponent::class)
            ->call('selectCustomer', $customer->id)
            ->assertDispatched('customerSelected');
    }

    // -------------------------------------------------------------------------
    // 8. Global guard deny-by-default for unlisted component/method
    // -------------------------------------------------------------------------

    public function test_global_guard_refuses_unlisted_component_method(): void
    {
        $this->actingAs($this->finance);

        // DummyReadOnlyTestComponent has harmlessAction() which does NOT call abort(403)
        // It must be refused with 403 by the global deny-by-default guard
        Livewire::test(DummyReadOnlyTestComponent::class)
            ->call('harmlessAction')
            ->assertForbidden();
    }
}
