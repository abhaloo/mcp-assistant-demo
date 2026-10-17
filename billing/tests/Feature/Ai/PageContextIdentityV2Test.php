<?php

declare(strict_types=1);

namespace Tests\Feature\Ai;

use App\Models\Department;
use App\Models\Entity;
use App\Models\User;
use App\Models\WorkOrder;
use App\Services\AskAi\ResolvedAskRequest;
use App\Services\RagClientInterface;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\DB;
use Spatie\Permission\Models\Permission;
use Spatie\Permission\PermissionRegistrar;
use Tests\Support\FakeRagStreamClient;
use Tests\TestCase;

final class PageContextIdentityV2Test extends TestCase
{
    use RefreshDatabase;

    private User $user;

    private Department $department;

    private string $validBundleHash = 'sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef';

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
     * @param  list<array{id: string, resource_type: string}>  $records
     * @return array<string, mixed>
     */
    private function clientContextV2(
        array $records,
        string $profile = 'jobs.show.v2',
        string $detailContract = 'typed-detail.v2',
        ?string $bundleHash = null,
        int $epoch = 2,
    ): array {
        return [
            'version' => 2,
            'profile' => $profile,
            'records' => $records,
            'detail_contract' => $detailContract,
            'definition_bundle_hash' => $bundleHash ?? $this->validBundleHash,
            'compatibility_epoch' => $epoch,
        ];
    }

    /** @test */
    public function resolves_v2_identity_payload_for_authorized_job_with_exact_wire_shape(): void
    {
        $job = WorkOrder::factory()->withDepartment($this->department)->create([
            'work_number' => 'W-24694',
            'title' => 'V2 Test Job',
            'entity_id' => 1,
            'ordered_qty' => '500 PCS',
            'no_pages' => '4',
        ]);

        $fake = new FakeRagStreamClient("event: done\ndata: {\"model\":\"m\"}\n\n");
        $this->app->instance(RagClientInterface::class, $fake);

        $payload = $this->clientContextV2([
            ['id' => (string) $job->id, 'resource_type' => 'job'],
        ]);

        $response = $this->actingAs($this->user)->postJson('/ai-search/stream', [
            'question' => 'Tell me about this job in v2',
            'page_context' => $payload,
        ]);

        $response->assertOk();
        $this->assertInstanceOf(ResolvedAskRequest::class, $fake->lastStreamRequest);

        $resolved = $fake->lastStreamRequest;
        $this->assertNotNull($resolved->pageContext);
        $this->assertSame('jobs', $resolved->effectiveContextMode());

        $wire = $resolved->pageContext->toWireArray();
        $this->assertSame(
            ['version', 'profile', 'records', 'detail_contract', 'definition_bundle_hash', 'compatibility_epoch'],
            array_keys($wire)
        );
        $this->assertSame(2, $wire['version']);
        $this->assertSame('jobs.show.v2', $wire['profile']);
        $this->assertSame('typed-detail.v2', $wire['detail_contract']);
        $this->assertSame($this->validBundleHash, $wire['definition_bundle_hash']);
        $this->assertSame(2, $wire['compatibility_epoch']);

        $this->assertCount(1, $wire['records']);
        $record = $wire['records'][0];
        $this->assertSame((string) $job->id, $record['id']);
        $this->assertSame(['id', 'label', 'link_key'], array_keys($record));
        $this->assertSame('#W-24694 — V2 Test Job', $record['label']);
        $this->assertSame('work_orders', $record['link_key']);
        $this->assertArrayNotHasKey('fields', $record);
    }

    /** @test */
    public function v2_payload_requires_exact_count_of_one_for_jobs_show_v2(): void
    {
        $fake = new FakeRagStreamClient("event: done\ndata: {\"model\":\"m\"}\n\n");
        $this->app->instance(RagClientInterface::class, $fake);

        $payload = $this->clientContextV2([
            ['id' => '1', 'resource_type' => 'job'],
            ['id' => '2', 'resource_type' => 'job'],
        ]);

        $response = $this->actingAs($this->user)->postJson('/ai-search/stream', [
            'question' => 'Summarize',
            'page_context' => $payload,
        ]);

        $response->assertStatus(422);
        $response->assertJson([
            'message' => 'Invalid page context.',
            'error' => 'invalid_page_context',
        ]);
        $this->assertNull($fake->lastStreamRequest);
    }

    /** @test */
    public function v2_payload_rejects_missing_or_unprefixed_definition_bundle_hash(): void
    {
        $fake = new FakeRagStreamClient("event: done\ndata: {\"model\":\"m\"}\n\n");
        $this->app->instance(RagClientInterface::class, $fake);

        $payload = $this->clientContextV2(
            [['id' => '1', 'resource_type' => 'job']],
            bundleHash: 'invalid_unprefixed_hash'
        );

        $response = $this->actingAs($this->user)->postJson('/ai-search/stream', [
            'question' => 'Summarize',
            'page_context' => $payload,
        ]);

        $response->assertStatus(422);
        $this->assertNull($fake->lastStreamRequest);
    }

    /** @test */
    public function v2_payload_returns_409_for_unauthorized_or_cross_entity_records(): void
    {
        Entity::factory()->create(['id' => 2]);
        $foreign = WorkOrder::factory()->withDepartment($this->department)->create(['entity_id' => 1]);
        DB::table('work_orders')->where('id', $foreign->id)->update(['entity_id' => 2]);

        $fake = new FakeRagStreamClient("event: done\ndata: {\"model\":\"m\"}\n\n");
        $this->app->instance(RagClientInterface::class, $fake);

        $payload = $this->clientContextV2([
            ['id' => (string) $foreign->id, 'resource_type' => 'job'],
        ]);

        $response = $this->actingAs($this->user)->postJson('/ai-search/stream', [
            'question' => 'Summarize this foreign job',
            'page_context' => $payload,
        ]);

        $response->assertStatus(409);
        $response->assertJson(['error' => 'stale_page_context']);
        $this->assertNull($fake->lastStreamRequest);
    }

    /** @test */
    public function v2_payload_rejects_unknown_keys(): void
    {
        $payload = $this->clientContextV2([['id' => '1', 'resource_type' => 'job']]);
        $payload['extra_unknown_key'] = 'malformed';

        $response = $this->actingAs($this->user)->postJson('/ai-search/stream', [
            'question' => 'Summarize',
            'page_context' => $payload,
        ]);

        $response->assertStatus(422);
    }
}
