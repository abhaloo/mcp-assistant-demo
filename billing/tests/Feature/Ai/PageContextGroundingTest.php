<?php

declare(strict_types=1);

namespace Tests\Feature\Ai;

use App\Models\Bill;
use App\Models\Customer;
use App\Models\CustomerOrder;
use App\Models\Department;
use App\Models\Entity;
use App\Models\User;
use App\Models\WorkOrder;
use App\Services\AskAi\PageContext\JobsContextProfile;
use App\Services\AskAi\ResolvedAskRequest;
use App\Services\RagClientInterface;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\Log;
use Spatie\Permission\Models\Permission;
use Spatie\Permission\PermissionRegistrar;
use Tests\Support\FakeRagStreamClient;
use Tests\TestCase;

class PageContextGroundingTest extends TestCase
{
    use RefreshDatabase;

    private User $user;

    private Department $department;

    protected function setUp(): void
    {
        parent::setUp();
        Cache::flush();

        Entity::factory()->create(['id' => 1]);
        $this->department = Department::factory()->create(['name' => 'Printing']);
        $this->user = User::factory()->create([
            'entity_id' => 1,
            'role' => 'admin',
            'department_id' => $this->department->id,
        ]);

        app(PermissionRegistrar::class)->forgetCachedPermissions();
        foreach (['view job', 'view customer', 'view invoice', 'view customer order'] as $perm) {
            Permission::firstOrCreate(
                ['name' => $perm, 'guard_name' => 'web'],
                ['group' => 'test']
            );
            $this->user->givePermissionTo($perm);
        }

        config([
            'services.rag.enabled' => true,
            'services.rag.url' => 'http://rag.test',
            'services.rag.jwt_secret' => 'page-context-test-secret',
        ]);
    }

    /**
     * @param  list<string>  $recordIds
     * @return array<string, mixed>
     */
    private function clientContext(
        array $recordIds,
        string $route = 'work-order.index',
        string $kind = 'list',
        ?string $scopeId = null,
        string $contextId = 'ctx-jobs-1',
    ): array {
        $payload = [
            'version' => JobsContextProfile::VERSION,
            'context_id' => $contextId,
            'route' => $route,
            'kind' => $kind,
            'resource_type' => JobsContextProfile::RESOURCE_TYPE,
            'record_ids' => $recordIds,
        ];
        if ($scopeId !== null) {
            $payload['scope_id'] = $scopeId;
        }

        return $payload;
    }

    /** @test */
    public function resolves_authorized_jobs_preserving_order_with_wire_shape(): void
    {
        $first = WorkOrder::factory()->withDepartment($this->department)->create([
            'title' => 'Alpha job',
            'work_number' => 'W-100',
            'priority' => 'high',
            'entity_id' => 1,
        ]);
        $second = WorkOrder::factory()->withDepartment($this->department)->create([
            'title' => 'Beta job',
            'work_number' => 'W-200',
            'entity_id' => 1,
        ]);

        $ids = [(string) $second->id, (string) $first->id];

        $fake = new FakeRagStreamClient(
            "event: token\ndata: {\"d\":\"ok\"}\n\n".
            "event: done\ndata: {\"model\":\"m\"}\n\n"
        );
        $this->app->instance(RagClientInterface::class, $fake);

        $response = $this->actingAs($this->user)->postJson('/ai-search/stream', [
            'question' => 'Summarize these jobs',
            'page_context' => $this->clientContext($ids),
        ]);

        $response->assertOk();
        $this->assertInstanceOf(ResolvedAskRequest::class, $fake->lastStreamRequest);

        $resolved = $fake->lastStreamRequest;
        $this->assertNotNull($resolved->pageContext);
        $this->assertSame('jobs', $resolved->effectiveContextMode());

        $wire = $resolved->pageContext->toWireArray();
        $this->assertSame(
            ['version', 'kind', 'resource_type', 'profile', 'title', 'records'],
            array_keys($wire)
        );
        $this->assertSame('jobs.index.v1', $wire['profile']);
        $this->assertArrayNotHasKey('route', $wire);
        $this->assertArrayNotHasKey('context_id', $wire);

        $recordIds = array_map(static fn ($r) => $r->id, $resolved->pageContext->records);
        $this->assertSame($ids, $recordIds);
        $this->assertSame('false', $wire['records'][0]['fields']['is_high_priority']);
        $this->assertSame('true', $wire['records'][1]['fields']['is_high_priority']);
    }

    /** @test */
    public function wire_fixture_matches_canonical_json_file(): void
    {
        $fixture = json_decode(
            file_get_contents(base_path('tests/fixtures/trusted_page_context_wire.json')),
            true,
            512,
            JSON_THROW_ON_ERROR
        );

        $this->assertSame(
            ['version', 'kind', 'resource_type', 'profile', 'title', 'records'],
            array_keys($fixture)
        );
        $this->assertSame('jobs.index.v1', $fixture['profile']);
    }

    /** @test */
    public function detail_profile_requires_exactly_one_id(): void
    {
        $wo = WorkOrder::factory()->withDepartment($this->department)->create(['entity_id' => 1]);

        $fake = new FakeRagStreamClient("event: done\ndata: {\"model\":\"m\"}\n\n");
        $this->app->instance(RagClientInterface::class, $fake);

        $response = $this->actingAs($this->user)->postJson('/ai-search/stream', [
            'question' => 'Tell me about this job',
            'page_context' => $this->clientContext(
                [(string) $wo->id],
                route: 'work-order.show',
                kind: 'detail',
                contextId: 'jobs:show:'.$wo->id,
            ),
        ]);

        $response->assertOk();
        $this->assertSame('jobs.show.v1', $fake->lastStreamRequest?->pageContext?->profile);
    }

    /** @test */
    public function detail_profile_rejects_wrong_count(): void
    {
        $fake = new FakeRagStreamClient("event: done\ndata: {\"model\":\"m\"}\n\n");
        $this->app->instance(RagClientInterface::class, $fake);

        $response = $this->actingAs($this->user)->postJson('/ai-search/stream', [
            'question' => 'Tell me about these jobs',
            'page_context' => $this->clientContext(
                ['1', '2'],
                route: 'work-order.show',
                kind: 'detail',
            ),
        ]);

        $response->assertStatus(422);
        $this->assertNull($fake->lastStreamRequest);
    }

    /** @test */
    public function bill_embedded_profile_requires_scope_and_parent_membership(): void
    {
        $bill = Bill::factory()->create(['entity_id' => 1]);
        $onBill = WorkOrder::factory()->withDepartment($this->department)->create([
            'entity_id' => 1,
            'bill_id' => $bill->id,
        ]);
        $otherBill = Bill::factory()->create(['entity_id' => 1]);
        $foreign = WorkOrder::factory()->withDepartment($this->department)->create([
            'entity_id' => 1,
            'bill_id' => $otherBill->id,
        ]);

        $fake = new FakeRagStreamClient("event: done\ndata: {\"model\":\"m\"}\n\n");
        $this->app->instance(RagClientInterface::class, $fake);

        $response = $this->actingAs($this->user)->postJson('/ai-search/stream', [
            'question' => 'Jobs on this bill',
            'page_context' => $this->clientContext(
                [(string) $onBill->id, (string) $foreign->id],
                route: 'bill.show',
                kind: 'embedded_list',
                scopeId: (string) $bill->id,
                contextId: 'jobs:bill:'.$bill->id,
            ),
        ]);

        $response->assertStatus(409);
        $response->assertJson(['error' => 'stale_page_context']);
        $this->assertNull($fake->lastStreamRequest);
    }

    /** @test */
    public function report_and_all_embedded_profiles_resolve_to_the_approved_server_profiles(): void
    {
        $customer = Customer::factory()->create(['entity_id' => 1]);
        $bill = Bill::factory()->create(['entity_id' => 1, 'customer_id' => $customer->id]);
        $order = CustomerOrder::factory()->create([
            'entity_id' => 1,
            'customer_id' => $customer->id,
        ]);
        $job = WorkOrder::factory()->withDepartment($this->department)->create([
            'entity_id' => 1,
            'customer_id' => $customer->id,
            'bill_id' => $bill->id,
            'customer_order_id' => $order->id,
        ]);

        $cases = [
            ['report.work-order', 'report', null, 'jobs.report.v1'],
            ['customer.show', 'embedded_list', (string) $customer->id, 'jobs.customer-list.v1'],
            ['bill.show', 'embedded_list', (string) $bill->id, 'jobs.bill-list.v1'],
            ['customer-orders.show', 'embedded_list', (string) $order->id, 'jobs.customer-order-list.v1'],
        ];

        $fake = new FakeRagStreamClient("event: done\ndata: {\"model\":\"m\"}\n\n");
        $this->app->instance(RagClientInterface::class, $fake);
        foreach ($cases as [$route, $kind, $scopeId, $expectedProfile]) {
            $response = $this->actingAs($this->user)->postJson('/ai-search/stream', [
                'question' => 'Summarize the visible jobs',
                'page_context' => $this->clientContext(
                    [(string) $job->id],
                    route: $route,
                    kind: $kind,
                    scopeId: $scopeId,
                    contextId: 'ctx-'.$expectedProfile,
                ),
            ]);

            $response->assertOk();
            $this->assertSame($expectedProfile, $fake->lastStreamRequest?->pageContext?->profile);
        }
    }

    /** @test */
    public function bill_embedded_context_still_enforces_department_visibility(): void
    {
        $bill = Bill::factory()->create(['entity_id' => 1]);
        $otherDepartment = Department::factory()->create();
        $job = WorkOrder::factory()->withDepartment($otherDepartment)->create([
            'entity_id' => 1,
            'bill_id' => $bill->id,
        ]);
        $fake = new FakeRagStreamClient("event: done\ndata: {\"model\":\"m\"}\n\n");
        $this->app->instance(RagClientInterface::class, $fake);

        $this->actingAs($this->user)->postJson('/ai-search/stream', [
            'question' => 'Summarize this bill',
            'page_context' => $this->clientContext(
                [(string) $job->id],
                route: 'bill.show',
                kind: 'embedded_list',
                scopeId: (string) $bill->id,
            ),
        ])->assertStatus(409)->assertJson(['error' => 'stale_page_context']);
    }

    /** @test */
    public function empty_page_context_resolves_with_empty_records(): void
    {
        $fake = new FakeRagStreamClient("event: done\ndata: {\"model\":\"m\"}\n\n");
        $this->app->instance(RagClientInterface::class, $fake);

        $response = $this->actingAs($this->user)->postJson('/ai-search/stream', [
            'question' => 'What is on this page?',
            'page_context' => $this->clientContext([]),
        ]);

        $response->assertOk();
        $resolved = $fake->lastStreamRequest;
        $this->assertNotNull($resolved?->pageContext);
        $this->assertSame([], $resolved->pageContext->records);
        $this->assertSame('jobs', $resolved->effectiveContextMode());
    }

    /** @test */
    public function unauthorized_or_missing_id_returns_409_without_revealing_which(): void
    {
        $owned = WorkOrder::factory()->withDepartment($this->department)->create(['entity_id' => 1]);
        $otherDept = Department::factory()->create(['name' => 'Sales']);
        $foreign = WorkOrder::factory()->withDepartment($otherDept)->create(['entity_id' => 1]);

        $fake = new FakeRagStreamClient("event: done\ndata: {\"model\":\"m\"}\n\n");
        $this->app->instance(RagClientInterface::class, $fake);

        $response = $this->actingAs($this->user)->postJson('/ai-search/stream', [
            'question' => 'Summarize these jobs',
            'page_context' => $this->clientContext([
                (string) $owned->id,
                (string) $foreign->id,
            ]),
        ]);

        $response->assertStatus(409);
        $response->assertJson(['error' => 'stale_page_context']);
        $body = $response->getContent();
        $this->assertStringNotContainsString((string) $foreign->id, $body);
        $this->assertStringNotContainsString((string) $owned->id, $body);
        $this->assertNull($fake->lastStreamRequest);
    }

    /** @test */
    public function user_without_view_job_permission_gets_409_for_visible_work_orders(): void
    {
        $wo = WorkOrder::factory()->withDepartment($this->department)->create(['entity_id' => 1]);

        $restricted = User::factory()->create([
            'entity_id' => 1,
            'role' => 'staff',
            'department_id' => $this->department->id,
        ]);
        // Same entity/department as $wo — passes WorkOrderVisibilityScope but lacks view job.
        $restricted->syncPermissions([]);

        $fake = new FakeRagStreamClient("event: done\ndata: {\"model\":\"m\"}\n\n");
        $this->app->instance(RagClientInterface::class, $fake);

        $response = $this->actingAs($restricted)->postJson('/ai-search/stream', [
            'question' => 'Summarize this job',
            'page_context' => $this->clientContext([(string) $wo->id]),
        ]);

        $response->assertStatus(409);
        $response->assertJson(['error' => 'stale_page_context']);
        $this->assertNull($fake->lastStreamRequest);
    }

    /** @test */
    public function unknown_page_context_keys_return_422(): void
    {
        $fake = new FakeRagStreamClient("event: done\ndata: {\"model\":\"m\"}\n\n");
        $this->app->instance(RagClientInterface::class, $fake);

        $payload = $this->clientContext([]);
        $payload['proof'] = 'not-allowed';

        $response = $this->actingAs($this->user)->postJson('/ai-search/stream', [
            'question' => 'Summarize these jobs',
            'page_context' => $payload,
        ]);

        $response->assertStatus(422);
        $this->assertNull($fake->lastStreamRequest);
    }

    /** @test */
    public function unsupported_route_tuple_returns_422(): void
    {
        $fake = new FakeRagStreamClient("event: done\ndata: {\"model\":\"m\"}\n\n");
        $this->app->instance(RagClientInterface::class, $fake);

        $payload = $this->clientContext([]);
        $payload['route'] = 'dashboard.index';

        $response = $this->actingAs($this->user)->postJson('/ai-search/stream', [
            'question' => 'Summarize these jobs',
            'page_context' => $payload,
        ]);

        $response->assertStatus(422);
        $this->assertNull($fake->lastStreamRequest);
    }

    /** @test */
    public function cross_entity_work_order_returns_409_without_revealing_which(): void
    {
        Entity::factory()->create(['id' => 2]);
        $owned = WorkOrder::factory()->withDepartment($this->department)->create(['entity_id' => 1]);
        // Bypass creating hooks that may stamp the acting user's entity_id.
        $foreign = WorkOrder::factory()->withDepartment($this->department)->create(['entity_id' => 1]);
        \DB::table('work_orders')->where('id', $foreign->id)->update(['entity_id' => 2]);

        $fake = new FakeRagStreamClient("event: done\ndata: {\"model\":\"m\"}\n\n");
        $this->app->instance(RagClientInterface::class, $fake);

        $response = $this->actingAs($this->user)->postJson('/ai-search/stream', [
            'question' => 'Summarize these jobs',
            'page_context' => $this->clientContext([
                (string) $owned->id,
                (string) $foreign->id,
            ]),
        ]);

        $response->assertStatus(409);
        $response->assertJson(['error' => 'stale_page_context']);
        $this->assertNull($fake->lastStreamRequest);
    }

    /** @test */
    public function json_fallback_receives_the_same_resolved_request(): void
    {
        $wo = WorkOrder::factory()->withDepartment($this->department)->create(['entity_id' => 1]);

        $fake = new FakeRagStreamClient(
            "event: error\ndata: {\"error\":\"non_streamable_query_type\",\"detail\":\"...\"}\n\n",
            [
                'question' => 'q',
                'answer' => 'Structured answer.',
                'sources' => [],
                'model' => 'gpt-4o-mini',
                'query_type' => 'structured',
            ]
        );
        $this->app->instance(RagClientInterface::class, $fake);

        $response = $this->actingAs($this->user)->post('/ai-search/stream', [
            'question' => 'How many of these are open?',
            'page_context' => $this->clientContext([(string) $wo->id]),
        ]);

        $response->assertOk();
        $response->baseResponse->sendContent();

        $this->assertNotNull($fake->lastStreamRequest);
        $this->assertNotNull($fake->lastAskRequest);
        $this->assertSame(
            $fake->lastStreamRequest->pageContext?->toWireArray(),
            $fake->lastAskRequest->pageContext?->toWireArray()
        );
    }

    /** @test */
    public function every_populated_source_column_reaches_the_wire_verbatim(): void
    {
        $job = WorkOrder::factory()->withDepartment($this->department)->create([
            'work_number' => 'W-777',
            'title' => 'Producer pin',
            'status' => 'IN PROGRESS',
            'priority' => 'high',
            'entity_id' => 1,
        ]);

        $fake = new FakeRagStreamClient("event: done\ndata: {\"model\":\"m\"}\n\n");
        $this->app->instance(RagClientInterface::class, $fake);

        $response = $this->actingAs($this->user)->postJson('/ai-search/stream', [
            'question' => 'Tell me about this job',
            'page_context' => $this->clientContext([(string) $job->id]),
        ]);

        $response->assertOk();
        $this->assertNotNull($fake->lastStreamRequest);
        $this->assertNotNull($fake->lastStreamRequest->pageContext);

        $records = $fake->lastStreamRequest->pageContext->records;
        $this->assertCount(1, $records);
        $fields = $records[0]->fields;

        foreach (JobsContextProfile::FIELD_ALLOWLIST as $key) {
            $this->assertArrayHasKey($key, $fields);
        }

        $this->assertSame('W-777', $fields['work_number']);
        $this->assertSame('Producer pin', $fields['title']);
        $this->assertSame('IN PROGRESS', $fields['status']);
        $this->assertSame('high', $fields['priority']);
        $this->assertSame('true', $fields['is_high_priority']);
        $this->assertSame($this->department->name, $fields['department']);
    }

    /** @test */
    public function non_null_delivery_dates_do_not_crash_the_resolver(): void
    {
        $job = WorkOrder::factory()->withDepartment($this->department)->create([
            'delivery_date' => '2026-09-01',
            'actual_delivery_date' => '2026-09-03',
            'entity_id' => 1,
        ]);

        $fake = new FakeRagStreamClient("event: done\ndata: {\"model\":\"m\"}\n\n");
        $this->app->instance(RagClientInterface::class, $fake);

        $response = $this->actingAs($this->user)->postJson('/ai-search/stream', [
            'question' => 'When is delivery?',
            'page_context' => $this->clientContext([(string) $job->id]),
        ]);

        $response->assertOk();
        $this->assertNotNull($fake->lastStreamRequest);
        $this->assertNotNull($fake->lastStreamRequest->pageContext);

        $records = $fake->lastStreamRequest->pageContext->records;
        $this->assertCount(1, $records);
        $fields = $records[0]->fields;

        $this->assertSame('2026-09-01', $fields['delivery']);
        $this->assertSame('2026-09-03', $fields['actual_delivery']);
    }

    /** @test */
    public function malformed_delivery_date_returns_null(): void
    {
        $job = WorkOrder::factory()->withDepartment($this->department)->create([
            'delivery_date' => '20204-01-26',
            'entity_id' => 1,
        ]);

        $fake = new FakeRagStreamClient("event: done\ndata: {\"model\":\"m\"}\n\n");
        $this->app->instance(RagClientInterface::class, $fake);

        $response = $this->actingAs($this->user)->postJson('/ai-search/stream', [
            'question' => 'When is delivery?',
            'page_context' => $this->clientContext([(string) $job->id]),
        ]);

        $response->assertOk();
        $this->assertNotNull($fake->lastStreamRequest);
        $this->assertNotNull($fake->lastStreamRequest->pageContext);

        $records = $fake->lastStreamRequest->pageContext->records;
        $this->assertCount(1, $records);
        $fields = $records[0]->fields;

        $this->assertNull($fields['delivery']);
    }

    /** @test */
    public function show_profile_carries_ordered_qty_and_no_pages_verbatim(): void
    {
        $job = WorkOrder::factory()->withDepartment($this->department)->create([
            'ordered_qty' => '100PCS X 4 TITLES; 50PCS X 2 DESIGNS; 25PCS EXTRA FOR STOCK',
            'no_pages' => '16+4 = 20',
            'entity_id' => 1,
        ]);

        $fake = new FakeRagStreamClient("event: done\ndata: {\"model\":\"m\"}\n\n");
        $this->app->instance(RagClientInterface::class, $fake);

        $response = $this->actingAs($this->user)->postJson('/ai-search/stream', [
            'question' => 'What is the ordered quantity and page count?',
            'page_context' => $this->clientContext(
                [(string) $job->id],
                route: 'work-order.show',
                kind: 'detail',
                contextId: 'jobs:show:'.$job->id,
            ),
        ]);

        $response->assertOk();
        $this->assertNotNull($fake->lastStreamRequest);
        $this->assertNotNull($fake->lastStreamRequest->pageContext);

        $records = $fake->lastStreamRequest->pageContext->records;
        $this->assertCount(1, $records);
        $record = $records[0];
        $fields = $record->fields;

        $this->assertSame('100PCS X 4 TITLES; 50PCS X 2 DESIGNS; 25PCS EXTRA FOR STOCK', $fields['ordered_qty'] ?? null);
        $this->assertSame('16+4 = 20', $fields['no_pages'] ?? null);
        $this->assertSame(255, JobsContextProfile::FIELD_CAPS['ordered_qty'] ?? null);
        $this->assertSame(255, JobsContextProfile::FIELD_CAPS['no_pages'] ?? null);
        $this->assertNotContains('ordered_qty', $record->truncatedFields);
        $this->assertNotContains('no_pages', $record->truncatedFields);
    }

    /** @test */
    public function invalid_page_context_returns_422_logs_reason_and_does_not_call_rag(): void
    {
        $spy = Log::spy();
        $spy->shouldReceive('channel')->andReturnSelf();

        $fake = new FakeRagStreamClient("event: done\ndata: {\"model\":\"m\"}\n\n");
        $this->app->instance(RagClientInterface::class, $fake);

        $payload = $this->clientContext([]);
        $payload['unknown_illegal_key'] = 'injection_attempt';

        $runId = '0123456789abcdef0123456789abcdef';

        $response = $this->actingAs($this->user)->postJson('/ai-search/stream', [
            'question' => 'Summarize these jobs',
            'run_id' => $runId,
            'page_context' => $payload,
        ]);

        $response->assertStatus(422);
        $response->assertJson([
            'message' => 'Invalid page context.',
            'error' => 'invalid_page_context',
        ]);
        $this->assertNull($fake->lastStreamRequest);

        $spy->shouldHaveReceived('channel')->with('ai-search');
        $spy->shouldHaveReceived('warning')->withArgs(function ($message, $context = []) use ($runId) {
            return $message === 'ai-search invalid page context'
                && is_array($context)
                && ($context['correlation_id'] ?? null) === $runId
                && ($context['user_id'] ?? null) === $this->user->id
                && ($context['category'] ?? null) === 'unknown_keys';
        });
    }
}
