<?php

declare(strict_types=1);

namespace Tests\Feature\Ai;

use App\Models\Entity;
use App\Models\User;
use App\Services\RagClientInterface;
use GuzzleHttp\Psr7\FnStream;
use GuzzleHttp\Psr7\Response as Psr7Response;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\Cache;
use Psr\Http\Message\ResponseInterface;
use Psr\Http\Message\StreamInterface;
use Tests\Support\CapturedStreamOutput;
use Tests\Support\FakeRagStreamClient;
use Tests\Support\RagClientStub;
use Tests\TestCase;

final class AiSearchV2DeadlineTest extends TestCase
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
            'services.rag.jwt_secret' => 'deadline-test-secret',
            'ask_ai.deadline_budget_ms' => 25000,
            'ask_ai.budget_multiplier' => 1,
            'ask_ai.ask_v2_enabled' => true,
        ]);
    }

    protected function tearDown(): void
    {
        Carbon::setTestNow();

        parent::tearDown();
    }

    /**
     * AC-09: a chunk that only becomes available after the shared deadline
     * has passed is discarded — never forwarded to the browser as trusted
     * terminal truth. Billing emits its own terminal stream_error instead.
     */
    public function test_late_result_is_discarded_after_shared_deadline(): void
    {
        $user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $this->actingAs($user);

        // A small budget so the test proves the real, live enforcement loop
        // without a multi-second sleep — generous enough to comfortably
        // clear normal request bootstrap overhead (auth, validation, JWT
        // mint) before the first frame, but far short of the delayed frame.
        config(['ask_ai.deadline_budget_ms' => 300]);

        $reads = 0;
        $stream = new FnStream([
            'eof' => function () use (&$reads): bool {
                return $reads >= 2;
            },
            'read' => function (int $length) use (&$reads): string {
                if ($reads === 0) {
                    $reads++;

                    return implode("\n", [
                        'event: activity',
                        'data: {"activity_id":"act-1","kind":"understand","state":"completed","elapsed_ms":5}',
                        '',
                        '',
                    ]);
                }

                // The terminal frame only becomes readable well after the
                // deadline budget above has already elapsed — a late result.
                usleep(500_000);
                $reads++;

                return implode("\n", [
                    'event: turn_outcome',
                    'data: {"outcome_type":"answered","trusted":true}',
                    '',
                    '',
                ]);
            },
            'close' => function (): void {},
        ]);

        $slowClient = new class($stream) extends RagClientStub
        {
            public function __construct(private readonly StreamInterface $stream) {}

            public function streamV2(array $payload, User $user): ResponseInterface
            {
                return new Psr7Response(200, [], $this->stream);
            }
        };
        $this->app->instance(RagClientInterface::class, $slowClient);

        $response = $this->postJson('/api/ai/ask/v2', [
            'protocol_version' => '2',
            'operation' => 'new_question',
            'run_id' => str_repeat('d', 32),
            'thread_id' => 'thread-deadline',
            'question' => 'Will this time out?',
            'idempotency_key' => 'idemp-deadline-1',
            'response_policy' => 'strict',
        ]);

        $response->assertOk();
        $output = CapturedStreamOutput::from($response);

        // The safe, on-time activity frame is delivered normally.
        $this->assertStringContainsString('event: activity', $output);

        // The late terminal is discarded — it must never reach the browser.
        $this->assertStringNotContainsString('event: turn_outcome', $output);

        // Billing emits its own terminal failure instead of silently hanging
        // or presenting the discarded frame as if it had arrived on time.
        $this->assertStringContainsString('event: stream_error', $output);
        $this->assertStringContainsString('"code":"deadline_exceeded"', $output);
        $this->assertStringContainsString('"retryable":true', $output);
        $this->assertStringContainsString('"run_id":"'.str_repeat('d', 32).'"', $output);
        $this->assertStringNotContainsString('"error":"deadline_exceeded"', $output);
    }

    /**
     * AC-09: a client-supplied deadline_at_ms is never honoured and never
     * silently substituted for a DIFFERENT, still browser-influenced value —
     * the value that crosses to RAG is always Billing's own, computed once
     * at server ingress after Form Request validation.
     */
    public function test_client_supplied_deadline_is_never_forwarded_to_rag(): void
    {
        $user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $this->actingAs($user);

        Carbon::setTestNow(Carbon::createFromTimestampMs(1_800_000_000_000));

        $fakeClient = new FakeRagStreamClient(implode("\n", [
            'event: turn_outcome',
            'data: {"outcome_type":"answered","trusted":true}',
            '',
            '',
        ]));
        $this->app->instance(RagClientInterface::class, $fakeClient);

        // An absurdly extended value — far beyond the 25s budget — proves
        // the server does not merely clamp/trust the client's number.
        $clientClaimedDeadline = 1_800_000_000_000 + 999_000_000;

        $response = $this->postJson('/api/ai/ask/v2', [
            'protocol_version' => '2',
            'operation' => 'new_question',
            'run_id' => str_repeat('e', 32),
            'thread_id' => 'thread-deadline-2',
            'question' => 'Can the browser extend my deadline?',
            'idempotency_key' => 'idemp-deadline-2',
            'response_policy' => 'strict',
            'deadline_at_ms' => $clientClaimedDeadline,
        ]);

        $response->assertOk();

        $forwarded = $fakeClient->lastStreamV2Payload['deadline_at_ms'] ?? null;
        $this->assertNotNull($forwarded);
        $this->assertNotSame($clientClaimedDeadline, $forwarded);
        $this->assertSame(1_800_000_000_000 + 25000, $forwarded);
    }

    /**
     * A request with no client-supplied deadline gets the identical
     * server-computed value — proving the field is not merely stripped, but
     * genuinely computed at ingress every time.
     */
    public function test_absent_client_deadline_still_gets_server_computed_value(): void
    {
        $user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $this->actingAs($user);

        Carbon::setTestNow(Carbon::createFromTimestampMs(1_800_000_000_000));

        $fakeClient = new FakeRagStreamClient(implode("\n", [
            'event: turn_outcome',
            'data: {"outcome_type":"answered","trusted":true}',
            '',
            '',
        ]));
        $this->app->instance(RagClientInterface::class, $fakeClient);

        $response = $this->postJson('/api/ai/ask/v2', [
            'protocol_version' => '2',
            'operation' => 'new_question',
            'run_id' => str_repeat('f', 32),
            'thread_id' => 'thread-deadline-3',
            'question' => 'No deadline field at all.',
            'idempotency_key' => 'idemp-deadline-3',
            'response_policy' => 'strict',
        ]);

        $response->assertOk();
        $this->assertSame(1_800_000_000_000 + 25000, $fakeClient->lastStreamV2Payload['deadline_at_ms'] ?? null);
    }

    public function test_budget_multiplier_scales_the_ingress_deadline(): void
    {
        $user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $this->actingAs($user);

        Carbon::setTestNow(Carbon::createFromTimestampMs(1_800_000_000_000));
        config(['ask_ai.budget_multiplier' => 6]);

        $fakeClient = new FakeRagStreamClient(implode("\n", [
            'event: turn_outcome',
            'data: {"outcome_type":"answered","trusted":true}',
            '',
            '',
        ]));
        $this->app->instance(RagClientInterface::class, $fakeClient);

        $response = $this->postJson('/api/ai/ask/v2', [
            'protocol_version' => '2',
            'operation' => 'new_question',
            'run_id' => str_repeat('f', 32),
            'thread_id' => 'thread-deadline-4',
            'question' => 'Scaled deadline.',
            'idempotency_key' => 'idemp-deadline-4',
            'response_policy' => 'strict',
        ]);

        $response->assertOk();
        $this->assertSame(1_800_000_000_000 + 150000, $fakeClient->lastStreamV2Payload['deadline_at_ms'] ?? null);
    }
}
