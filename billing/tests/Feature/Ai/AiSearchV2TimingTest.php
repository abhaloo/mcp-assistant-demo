<?php

declare(strict_types=1);

namespace Tests\Feature\Ai;

use App\Models\Entity;
use App\Models\User;
use App\Services\RagClientInterface;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Cache;
use Tests\Support\FakeRagStreamClient;
use Tests\TestCase;

final class AiSearchV2TimingTest extends TestCase
{
    use RefreshDatabase;

    protected function setUp(): void
    {
        parent::setUp();

        Cache::flush();

        Entity::factory()->create(['id' => 1]);

        config([
            'services.rag.enabled' => true,
            'services.rag.url' => 'http://rag.test',
            'services.rag.jwt_secret' => 'timing-v2-test-secret',
        ]);
    }

    /**
     * AC-16: Billing/eval retain ui_first_activity_ms, ui_first_progress_ms
     * (an ALIAS of first activity, not a second event kind), ui_first_row_ms,
     * and ui_completion_latency_ms from the UI's own clock — kept distinct
     * from the server's own completion_latency_ms — with the correlation id
     * preserved end to end.
     */
    public function test_v2_timing_receipt_preserves_ui_milestones(): void
    {
        $user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $this->actingAs($user);

        $fakeClient = new FakeRagStreamClient('');
        $this->app->instance(RagClientInterface::class, $fakeClient);

        $traceId = str_repeat('d', 32);

        $response = $this->postJson('/ai-search/timing', [
            'trace_id' => $traceId,
            'feedback_token' => 'signed-feedback-token-v2',
            'ui_first_text_ms' => 120,
            'completion_latency_ms' => 900,
            'ui_first_activity_ms' => 80,
            'ui_first_row_ms' => 300,
            'ui_completion_latency_ms' => 950,
        ]);

        $response->assertOk();

        $payload = $fakeClient->lastTimingPayload;
        $this->assertNotNull($payload);

        // Correlation id preserved end to end.
        $this->assertSame($traceId, $payload['trace_id']);

        foreach (['ui_first_activity_ms', 'ui_first_progress_ms', 'ui_first_row_ms', 'ui_completion_latency_ms'] as $field) {
            $this->assertArrayHasKey($field, $payload, "Timing receipt must carry {$field}.");
        }

        $this->assertSame(80, $payload['ui_first_activity_ms']);

        // ui_first_progress_ms is an ALIAS of first activity, not a second
        // independently-submitted event kind — Billing derives it from the
        // same measurement rather than trusting a value that could disagree.
        $this->assertSame($payload['ui_first_activity_ms'], $payload['ui_first_progress_ms']);

        $this->assertSame(300, $payload['ui_first_row_ms']);
        $this->assertSame(950, $payload['ui_completion_latency_ms']);

        // Distinct clock sources: the UI-submitted completion figure is
        // never conflated with or overwritten by the server's own timing.
        $this->assertArrayHasKey('completion_latency_ms', $payload);
        $this->assertSame(900, $payload['completion_latency_ms']);
        $this->assertNotSame($payload['completion_latency_ms'], $payload['ui_completion_latency_ms']);
    }

    /**
     * The v2 UI fields are optional — a legacy-shaped payload (no v2 fields
     * at all) still succeeds and forwards only the legacy fields, proving
     * the v2 receipt is additive, not a breaking change to the existing
     * timing contract.
     */
    public function test_legacy_timing_payload_without_v2_fields_still_succeeds(): void
    {
        $user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $this->actingAs($user);

        $fakeClient = new FakeRagStreamClient('');
        $this->app->instance(RagClientInterface::class, $fakeClient);

        $traceId = str_repeat('e', 32);

        $this->postJson('/ai-search/timing', [
            'trace_id' => $traceId,
            'feedback_token' => 'signed-feedback-token-legacy',
            'ui_first_text_ms' => 100,
            'completion_latency_ms' => 400,
        ])->assertOk();

        $payload = $fakeClient->lastTimingPayload;
        $this->assertNotNull($payload);
        $this->assertSame($traceId, $payload['trace_id']);
        $this->assertArrayNotHasKey('ui_first_activity_ms', $payload);
        $this->assertArrayNotHasKey('ui_first_progress_ms', $payload);
        $this->assertArrayNotHasKey('ui_first_row_ms', $payload);
        $this->assertArrayNotHasKey('ui_completion_latency_ms', $payload);
    }
}
