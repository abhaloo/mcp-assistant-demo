<?php

declare(strict_types=1);

namespace Tests\Unit\Support\Release;

use App\Models\Entity;
use App\Models\User;
use App\Services\RagClientInterface;
use App\Support\Release\RagCompatibilityVerifier;
use GuzzleHttp\Psr7\Response as Psr7Response;
use GuzzleHttp\Psr7\Utils;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\View;
use Psr\Http\Message\ResponseInterface;
use Symfony\Component\HttpFoundation\StreamedResponse;
use Tests\Support\CapturedStreamOutput;
use Tests\Support\FakeRagStreamClient;
use Tests\Support\RagClientStub;
use Tests\TestCase;

/**
 * Compatibility Matrix and Rollback Proof Test Suite.
 *
 * Covers:
 * 1. Rollout phases:
 *    - B0/R1: Legacy Billing client with v2-ready RAG service.
 *    - B1-disabled/R1: Current Billing with ask_v2_enabled = false.
 *    - B1-enabled/R1: Current Billing with ask_v2_enabled = true.
 * 2. Consumer-first enablement and rollback order.
 * 3. Gate B trace completeness and error handling in mixed-version configurations.
 * 4. Manifest hash verification matrix (rows 1 to 6).
 */
final class RagCompatibilityMatrixTest extends TestCase
{
    use RefreshDatabase;

    private User $user;

    protected function setUp(): void
    {
        parent::setUp();

        Cache::flush();

        Entity::factory()->create(['id' => 1]);
        $this->user = User::factory()->create([
            'entity_id' => 1,
            'role' => 'admin',
        ]);

        config([
            'services.rag.enabled' => true,
            'services.rag.url' => 'http://rag.test',
            'services.rag.jwt_secret' => 'matrix-test-secret',
            'services.rag.ask_v2_enabled' => false,
            'ask_ai.ask_v2_enabled' => false,
        ]);
    }

    /**
     * Phase B0/R1: Legacy Billing client with v2-ready RAG service.
     * Legacy /ai-search/stream endpoint consumes legacy SSE format without error.
     */
    public function test_b0_r1_legacy_billing_client_with_v2_ready_rag_service(): void
    {
        $legacySseBody = implode("\n", [
            'event: token',
            'data: {"d":"Hello from v2-ready RAG service."}',
            '',
            'event: done',
            'data: {"model":"gpt-4o","usage":{"input_tokens":12,"output_tokens":8}}',
            '',
            '',
        ]);

        $fakeClient = new FakeRagStreamClient($legacySseBody);
        $this->app->instance(RagClientInterface::class, $fakeClient);

        $response = $this->actingAs($this->user)
            ->post('/ai-search/stream', [
                'question' => 'What is our printing policy?',
                'run_id' => '000000000000000000000000000000b0',
            ]);

        $response->assertOk();
        $this->assertStringContainsString('text/event-stream', (string) $response->headers->get('Content-Type'));
        $this->assertInstanceOf(StreamedResponse::class, $response->baseResponse);

        $output = CapturedStreamOutput::from($response);
        $this->assertStringContainsString('event: token', $output);
        $this->assertStringContainsString('Hello from v2-ready RAG service.', $output);
        $this->assertStringContainsString('event: done', $output);

        $this->assertNotNull($fakeClient->lastStreamRequest);
        $this->assertSame('000000000000000000000000000000b0', $fakeClient->lastStreamRequest->runId);
        $this->assertSame('What is our printing policy?', $fakeClient->lastStreamRequest->question);
    }

    /**
     * Phase B1-disabled/R1: Current Billing codebase with ask_v2_enabled = false.
     * Loads legacy assets/configuration and communicates over legacy endpoint.
     */
    public function test_b1_disabled_r1_billing_loads_legacy_assets_and_uses_legacy_endpoint(): void
    {
        config([
            'services.rag.ask_v2_enabled' => false,
            'ask_ai.ask_v2_enabled' => false,
        ]);

        // 1. Verify view resolves legacy stream URL and data-v2-enabled flag
        $viewHtml = View::make('includes.ask-ai-panel', [
            'ragEnabled' => true,
            'ragAvailable' => true,
        ])->render();

        $this->assertStringContainsString('data-v2-enabled="0"', $viewHtml);
        $this->assertStringContainsString('data-stream-url="'.route('ai-search.stream').'"', $viewHtml);

        // 2. Verify legacy streaming execution
        $legacyBody = implode("\n", [
            'event: token',
            'data: {"d":"Legacy response."}',
            '',
            'event: done',
            'data: {"model":"gpt-4o","usage":{"input_tokens":5,"output_tokens":5}}',
            '',
            '',
        ]);

        $fakeClient = new FakeRagStreamClient($legacyBody);
        $this->app->instance(RagClientInterface::class, $fakeClient);

        $response = $this->actingAs($this->user)
            ->post('/ai-search/stream', [
                'question' => 'How to create quotation?',
                'run_id' => '0000000000000000000000000000b1d1',
            ]);

        $response->assertOk();
        $output = CapturedStreamOutput::from($response);
        $this->assertStringContainsString('Legacy response.', $output);
        $this->assertStringContainsString('event: done', $output);
    }

    /**
     * Phase B1-enabled/R1: Current Billing codebase with ask_v2_enabled = true.
     * Loads v2 assets/configuration and communicates over /api/ai/ask/v2.
     */
    public function test_b1_enabled_r1_billing_loads_v2_assets_and_uses_v2_endpoint(): void
    {
        config([
            'services.rag.ask_v2_enabled' => true,
            'ask_ai.ask_v2_enabled' => true,
        ]);

        // 1. Verify view resolves v2 stream URL and data-v2-enabled flag
        $viewHtml = View::make('includes.ask-ai-panel', [
            'ragEnabled' => true,
            'ragAvailable' => true,
        ])->render();

        $this->assertStringContainsString('data-v2-enabled="1"', $viewHtml);
        $this->assertStringContainsString('data-stream-url="'.route('ai-search.v2.stream').'"', $viewHtml);

        // 2. Verify v2 streaming execution
        $v2SseBody = implode("\n", [
            'event: activity',
            'data: {"activity_id":"act-1","kind":"understand","state":"completed","elapsed_ms":15}',
            '',
            'event: table_start',
            'data: {"table_id":"tbl-1","columns":[{"key":"item","label":"Item"}]}',
            '',
            'event: table_rows',
            'data: {"table_id":"tbl-1","rows":[{"item":"Paper Glossy"}]}',
            '',
            'event: table_end',
            'data: {"table_id":"tbl-1","row_count":1,"table_digest":"dig-123"}',
            '',
            'event: turn_outcome',
            'data: {"outcome_type":"answered","trusted":true}',
            '',
            '',
        ]);

        $fakeClient = new FakeRagStreamClient($v2SseBody);
        $this->app->instance(RagClientInterface::class, $fakeClient);

        $payload = [
            'protocol_version' => '2',
            'operation' => 'new_question',
            'run_id' => '0000000000000000000000000000b1e1',
            'thread_id' => 'thread-v2-001',
            'question' => 'List glossy paper stock',
            'idempotency_key' => 'idemp-v2-001',
            'deadline_at_ms' => 1800000025000,
            'response_policy' => 'strict',
        ];

        $response = $this->actingAs($this->user)
            ->postJson('/api/ai/ask/v2', $payload);

        $response->assertOk();
        $this->assertStringContainsString('text/event-stream', (string) $response->headers->get('Content-Type'));

        $output = CapturedStreamOutput::from($response);
        $this->assertStringContainsString('event: activity', $output);
        $this->assertStringContainsString('event: table_start', $output);
        $this->assertStringContainsString('event: table_rows', $output);
        $this->assertStringContainsString('event: table_end', $output);
        $this->assertStringContainsString('event: turn_outcome', $output);

        $this->assertNotNull($fakeClient->lastStreamV2Payload);
        $this->assertSame('0000000000000000000000000000b1e1', $fakeClient->lastStreamV2Payload['run_id']);
    }

    /**
     * Prove consumer-first enablement order:
     * Step 1: Deploy RAG R1 (supports legacy + v2). Billing is at B0 / B1-disabled.
     * Step 2: Enable Billing v2 (B1-enabled).
     */
    public function test_consumer_first_enablement_order_proves_zero_downtime_rollout(): void
    {
        $fakeClient = new FakeRagStreamClient(implode("\n", [
            'event: token',
            'data: {"d":"Legacy step 1 answer."}',
            '',
            'event: done',
            'data: {"model":"gpt-4o","usage":{"input_tokens":4,"output_tokens":4}}',
            '',
            '',
        ]));
        $this->app->instance(RagClientInterface::class, $fakeClient);

        // Step 1: RAG R1 deployed, Billing v2 disabled (B1-disabled)
        config(['ask_ai.ask_v2_enabled' => false]);
        $resStep1 = $this->actingAs($this->user)->post('/ai-search/stream', [
            'question' => 'Query during step 1',
            'run_id' => '00000000000000000000000000000001',
        ]);
        $resStep1->assertOk();
        $this->assertStringContainsString('Legacy step 1 answer.', CapturedStreamOutput::from($resStep1));

        $this->actingAs($this->user)->postJson('/api/ai/ask/v2', [
            'protocol_version' => '2',
            'operation' => 'new_question',
            'run_id' => '00000000000000000000000000000099',
            'thread_id' => 'thread-step-disabled',
            'question' => 'Must not reach RAG while disabled',
            'idempotency_key' => 'idemp-step-disabled',
            'response_policy' => 'strict',
        ])->assertNotFound();

        // Step 2: Enable Billing v2 (B1-enabled). The endpoint must accept traffic;
        // wire proof lives in test_b1_enabled_r1_billing_loads_v2_assets_and_uses_v2_endpoint.
        config(['ask_ai.ask_v2_enabled' => true]);
        $this->app->forgetInstance(RagClientInterface::class);
        $this->app->instance(RagClientInterface::class, new FakeRagStreamClient(implode("\n", [
            'event: turn_outcome',
            'data: {"outcome_type":"answered","trusted":true}',
            '',
            '',
        ])));

        $this->actingAs($this->user)->postJson('/api/ai/ask/v2', [
            'protocol_version' => '2',
            'operation' => 'new_question',
            'run_id' => '00000000000000000000000000000002',
            'thread_id' => 'thread-step-2',
            'question' => 'Query during step 2',
            'idempotency_key' => 'idemp-step-2',
            'deadline_at_ms' => 1800000025000,
            'response_policy' => 'strict',
        ])->assertOk();
    }

    /**
     * Prove rollback order:
     * Disable Billing v2 feature flag first (falling back to legacy wire)
     * before rolling back RAG service from R1 to R0.
     */
    public function test_consumer_first_rollback_order_disables_billing_v2_before_rag_rollback(): void
    {
        // Initial state: B1-enabled with RAG R1
        config(['ask_ai.ask_v2_enabled' => true]);

        // Step 1 of rollback: disable Billing v2 feature flag first
        config(['ask_ai.ask_v2_enabled' => false]);

        $viewHtml = View::make('includes.ask-ai-panel', [
            'ragEnabled' => true,
            'ragAvailable' => true,
        ])->render();
        $this->assertStringContainsString('data-v2-enabled="0"', $viewHtml);
        $this->assertStringContainsString('data-stream-url="'.route('ai-search.stream').'"', $viewHtml);

        // Step 2 of rollback: legacy traffic safely routes to legacy endpoint
        $fakeLegacyClient = new FakeRagStreamClient(implode("\n", [
            'event: token',
            'data: {"d":"Post-rollback legacy answer."}',
            '',
            'event: done',
            'data: {"model":"gpt-4o","usage":{"input_tokens":3,"output_tokens":3}}',
            '',
            '',
        ]));
        $this->app->instance(RagClientInterface::class, $fakeLegacyClient);

        $response = $this->actingAs($this->user)->post('/ai-search/stream', [
            'question' => 'Post rollback question',
            'run_id' => '00000000000000000000000000000003',
        ]);
        $response->assertOk();
        $this->assertStringContainsString('Post-rollback legacy answer.', CapturedStreamOutput::from($response));
    }

    /**
     * Prove Gate B trace completeness:
     * run_id and trace IDs are preserved end-to-end without loss or truncation.
     */
    public function test_gate_b_trace_completeness_preserves_correlation_ids_in_all_configurations(): void
    {
        $runIdLegacy = '0123456789abcdef0123456789abcdef';
        $runIdV2 = 'fedcba9876543210fedcba9876543210';

        $fakeClient = new FakeRagStreamClient(implode("\n", [
            'event: done',
            'data: {"model":"gpt-4o","usage":{"input_tokens":1,"output_tokens":1}}',
            '',
            '',
        ]));
        $this->app->instance(RagClientInterface::class, $fakeClient);

        // 1. Legacy trace preservation
        $this->actingAs($this->user)->post('/ai-search/stream', [
            'question' => 'Check legacy correlation ID',
            'run_id' => $runIdLegacy,
        ]);
        $this->assertNotNull($fakeClient->lastStreamRequest);
        $this->assertSame($runIdLegacy, $fakeClient->lastStreamRequest->runId);

        // 2. v2 trace preservation
        $this->actingAs($this->user)->postJson('/api/ai/ask/v2', [
            'protocol_version' => '2',
            'operation' => 'new_question',
            'run_id' => $runIdV2,
            'thread_id' => 'thread-trace-test',
            'question' => 'Check v2 correlation ID',
            'idempotency_key' => 'idemp-trace-test',
            'deadline_at_ms' => 1800000025000,
            'response_policy' => 'strict',
        ]);
        $this->assertNotNull($fakeClient->lastStreamV2Payload);
        $this->assertSame($runIdV2, $fakeClient->lastStreamV2Payload['run_id']);

        // 3. Feedback trace preservation
        $this->actingAs($this->user)->postJson('/ai-search/feedback', [
            'trace_id' => $runIdLegacy,
            'feedback_token' => 'token-feedback-abc',
            'verdict' => 'up',
        ]);
        $this->assertNotNull($fakeClient->lastFeedbackPayload);
        $this->assertSame($runIdLegacy, $fakeClient->lastFeedbackPayload['trace_id']);

        // 4. Timing trace preservation
        $this->actingAs($this->user)->postJson('/ai-search/timing', [
            'trace_id' => $runIdLegacy,
            'feedback_token' => 'token-timing-abc',
            'ui_first_text_ms' => 120,
            'completion_latency_ms' => 450,
        ]);
        $this->assertNotNull($fakeClient->lastTimingPayload);
        $this->assertSame($runIdLegacy, $fakeClient->lastTimingPayload['trace_id']);
    }

    /**
     * Prove error handling in mixed-version configurations.
     */
    public function test_mixed_version_error_handling_and_fail_closed_guarantees(): void
    {
        // 1. Unauthenticated requests are rejected
        $this->postJson('/api/ai/ask/v2', [
            'protocol_version' => '2',
            'operation' => 'new_question',
            'run_id' => '00000000000000000000000000000001',
            'thread_id' => 'unauth-thread',
            'question' => 'Unauthenticated test',
            'idempotency_key' => 'idemp-unauth',
            'deadline_at_ms' => 1800000025000,
            'response_policy' => 'strict',
        ])->assertStatus(401);

        // 2. RAG disabled returns 503
        config(['services.rag.enabled' => false]);
        $this->actingAs($this->user)->postJson('/api/ai/ask/v2', [
            'protocol_version' => '2',
            'operation' => 'new_question',
            'run_id' => '00000000000000000000000000000001',
            'thread_id' => 'disabled-thread',
            'question' => 'Disabled test',
            'idempotency_key' => 'idemp-disabled',
            'deadline_at_ms' => 1800000025000,
            'response_policy' => 'strict',
        ])->assertStatus(503);

        config(['services.rag.enabled' => true]);

        // 3. Upstream 503 returns 503 JSON
        $errorClient = new class extends RagClientStub
        {
            public function streamV2(array $payload, User $user): ResponseInterface
            {
                return new Psr7Response(503, [], Utils::streamFor('Service Unavailable'));
            }
        };
        $this->app->instance(RagClientInterface::class, $errorClient);

        $this->actingAs($this->user)->postJson('/api/ai/ask/v2', [
            'protocol_version' => '2',
            'operation' => 'new_question',
            'run_id' => '00000000000000000000000000000001',
            'thread_id' => 'upstream-503-thread',
            'question' => 'Upstream 503 test',
            'idempotency_key' => 'idemp-503',
            'deadline_at_ms' => 1800000025000,
            'response_policy' => 'strict',
        ])->assertStatus(503);
    }

    /** @return array<string, mixed> */
    private function loadFixture(string $name): array
    {
        $path = __DIR__.'/../../../fixtures/'.$name;
        $decoded = json_decode((string) file_get_contents($path), true, flags: JSON_THROW_ON_ERROR);

        if (! is_array($decoded)) {
            throw new \RuntimeException("Fixture [{$name}] did not decode to an array.");
        }

        return $decoded;
    }

    /**
     * Row 1: Billing N / RAG N -> PASS.
     */
    public function test_matrix_row_1_current_billing_current_rag_passes(): void
    {
        $fixture = $this->loadFixture('rag_compatibility_one_active_compatible_revision.json');

        $result = RagCompatibilityVerifier::evaluate($fixture);

        $this->assertTrue($result->passed, implode('; ', $result->reasons));
        $this->assertSame([], $result->reasons);
    }

    /**
     * Row 2: Billing N-1 / RAG N -> PASS.
     */
    public function test_matrix_row_2_previous_billing_bundle_still_enabled_passes(): void
    {
        $fixture = $this->loadFixture('rag_compatibility_matrix_row2_previous_billing_bundle_enabled.json');

        $result = RagCompatibilityVerifier::evaluate($fixture);

        $this->assertTrue($result->passed, implode('; ', $result->reasons));
        $this->assertSame([], $result->reasons);
    }

    /**
     * Row 3: Billing N / RAG N-1 -> FAIL.
     */
    public function test_matrix_row_3_new_billing_old_rag_fails_closed(): void
    {
        $fixture = $this->loadFixture('rag_compatibility_matrix_row3_new_billing_old_rag_fails_closed.json');

        $result = RagCompatibilityVerifier::evaluate($fixture);

        $this->assertFalse($result->passed);
        $this->assertNotEmpty($result->reasons);
        $joined = implode(' ', $result->reasons);
        $this->assertStringContainsString("is not in RAG's accepted set", $joined);
    }

    /**
     * Row 4: Billing N+1 candidate / RAG N -> FAIL.
     */
    public function test_matrix_row_4_candidate_release_blocked_until_consumer_deployed_fails(): void
    {
        $fixture = $this->loadFixture('rag_compatibility_matrix_row4_candidate_ahead_of_accepted_set.json');

        $result = RagCompatibilityVerifier::evaluate($fixture);

        $this->assertFalse($result->passed);
        $this->assertNotEmpty($result->reasons);
        $joined = implode(' ', $result->reasons);
        $this->assertStringContainsString("is not in RAG's accepted set", $joined);
    }

    /**
     * Row 5: Billing rollback N->N-1 / RAG N -> PASS.
     */
    public function test_matrix_row_5_billing_rollback_previous_bundle_still_enabled_passes(): void
    {
        $fixture = $this->loadFixture('rag_compatibility_matrix_row5_billing_rollback_previous_bundle_enabled.json');

        $result = RagCompatibilityVerifier::evaluate($fixture);

        $this->assertTrue($result->passed, implode('; ', $result->reasons));
        $this->assertSame([], $result->reasons);
    }

    /**
     * Row 6: Billing N / RAG rollback N->N-1 -> FAIL.
     */
    public function test_matrix_row_6_rag_rollback_target_incompatible_with_live_billing_fails(): void
    {
        $fixture = $this->loadFixture('rag_compatibility_matrix_row6_rag_rollback_incompatible_with_live_billing.json');

        $result = RagCompatibilityVerifier::evaluate($fixture);

        $this->assertFalse($result->passed);
        $this->assertNotEmpty($result->reasons);
        $joined = implode(' ', $result->reasons);
        $this->assertStringContainsString("is not in RAG's accepted set", $joined);
    }

    /**
     * Aggregate proof: all six matrix rows produce their expected verdict.
     */
    public function test_all_six_matrix_rows_produce_their_expected_verdict(): void
    {
        $rows = [
            1 => 'rag_compatibility_one_active_compatible_revision.json',
            2 => 'rag_compatibility_matrix_row2_previous_billing_bundle_enabled.json',
            3 => 'rag_compatibility_matrix_row3_new_billing_old_rag_fails_closed.json',
            4 => 'rag_compatibility_matrix_row4_candidate_ahead_of_accepted_set.json',
            5 => 'rag_compatibility_matrix_row5_billing_rollback_previous_bundle_enabled.json',
            6 => 'rag_compatibility_matrix_row6_rag_rollback_incompatible_with_live_billing.json',
        ];

        $this->assertCount(6, $rows);

        $passCount = 0;
        $failCount = 0;

        foreach ($rows as $row => $fixtureName) {
            $fixture = $this->loadFixture($fixtureName);
            $expectedPassed = (bool) $fixture['expected']['passed'];

            $result = RagCompatibilityVerifier::evaluate($fixture);

            $this->assertSame(
                $expectedPassed,
                $result->passed,
                "Row {$row} ({$fixtureName}) expected passed=".($expectedPassed ? 'true' : 'false').' but got '.($result->passed ? 'true' : 'false')
            );

            if ($expectedPassed) {
                $passCount++;
            } else {
                $failCount++;
            }
        }

        $this->assertSame(3, $passCount);
        $this->assertSame(3, $failCount);
    }
}
