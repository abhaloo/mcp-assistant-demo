<?php

declare(strict_types=1);

namespace Tests\Feature\Ai;

use App\Models\Entity;
use App\Models\User;
use App\Services\AiCreditsService;
use App\Services\AskAi\ResolvedAskRequest;
use App\Services\RagClientInterface;
use GuzzleHttp\Psr7\Response as Psr7Response;
use GuzzleHttp\Psr7\Utils;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\Log;
use Illuminate\Testing\TestResponse;
use Psr\Http\Message\ResponseInterface;
use RuntimeException;
use Symfony\Component\HttpFoundation\StreamedResponse;
use Tests\Support\CapturedStreamOutput;
use Tests\Support\FakeRagStreamClient;
use Tests\Support\RagClientStub;
use Tests\TestCase;

class AiSearchStreamTest extends TestCase
{
    use RefreshDatabase;

    protected User $user;

    protected function setUp(): void
    {
        parent::setUp();

        // Credits usage + RAG cooldown live in the cache (array driver persists
        // across test methods in one process); flush so state doesn't bleed.
        Cache::flush();

        Entity::factory()->create(['id' => 1]);
        $this->user = User::factory()->create(['entity_id' => 1, 'role' => 'admin']);

        config([
            'services.rag.enabled' => true,
            'services.rag.url' => 'http://rag.test',
            'services.rag.jwt_secret' => 'stream-test-secret',
        ]);
    }

    /** @test */
    public function it_streams_upstream_sse_bytes_verbatim_with_correct_headers(): void
    {
        $body = "event: token\ndata: {\"d\":\"Hello\"}\n\n".
            "event: done\ndata: {\"model\":\"test-model\",\"usage\":{\"input_tokens\":1,\"output_tokens\":2}}\n\n";

        $this->app->instance(RagClientInterface::class, new FakeRagStreamClient($body));

        $response = $this->actingAs($this->user)
            ->post('/ai-search/stream', ['question' => 'What is MCP?']);

        $response->assertOk();
        $this->assertStringContainsString('text/event-stream', (string) $response->headers->get('Content-Type'));
        $response->assertHeader('X-Accel-Buffering', 'no');
        $this->assertFalse($response->headers->has('Content-Length'));
        $this->assertInstanceOf(
            StreamedResponse::class,
            $response->baseResponse
        );
    }

    /** @test */
    public function unauthenticated_requests_are_rejected(): void
    {
        $response = $this->post('/ai-search/stream', ['question' => 'test query']);

        $response->assertRedirect('/login');
    }

    /** @test */
    public function it_returns_503_when_rag_is_disabled(): void
    {
        config(['services.rag.enabled' => false]);

        $response = $this->actingAs($this->user)
            ->postJson('/ai-search/stream', ['question' => 'test query']);

        $response->assertStatus(503);
    }

    /**
     * B2 regression: a `done` event WITHOUT a usage field must still charge,
     * via the ceil(chars/4) estimate path in AiCreditsService.
     *
     * @test
     */
    public function it_charges_on_done_without_usage_via_char_estimate(): void
    {
        $body = "event: token\ndata: {\"d\":\"Hello there friend\"}\n\n".
            "event: done\ndata: {\"model\":\"m\"}\n\n";

        $this->app->instance(RagClientInterface::class, new FakeRagStreamClient($body));

        $response = $this->actingAs($this->user)
            ->post('/ai-search/stream', ['question' => 'estimate path?']);

        $response->assertOk();
        // Force the StreamedResponse closure to run (credits + telemetry fire here).
        $this->runStream($response);

        $this->assertGreaterThan(0, app(AiCreditsService::class)->currentUsage($this->user));
    }

    /** @test */
    public function it_charges_on_done_with_explicit_usage(): void
    {
        $body = "event: token\ndata: {\"d\":\"Hi\"}\n\n".
            "event: done\ndata: {\"model\":\"m\",\"usage\":{\"input_tokens\":1000,\"output_tokens\":500}}\n\n";

        $this->app->instance(RagClientInterface::class, new FakeRagStreamClient($body));

        $service = app(AiCreditsService::class);
        $before = $service->currentUsage($this->user);

        $response = $this->actingAs($this->user)
            ->post('/ai-search/stream', ['question' => 'usage path?']);

        $response->assertOk();
        $this->runStream($response);

        $this->assertGreaterThan($before, $service->currentUsage($this->user));
    }

    /**
     * Non-2xx upstream status: controller returns mapped JSON (NOT a stream)
     * and marks the service unavailable. See AiSearchController::mapUpstreamError.
     *
     * @test
     */
    public function upstream_non_2xx_returns_mapped_json_and_marks_unavailable(): void
    {
        $fake = new class extends RagClientStub
        {
            public function stream(ResolvedAskRequest $request): ResponseInterface
            {
                return new Psr7Response(503, [], 'upstream down');
            }

            public function ask(ResolvedAskRequest $request): array
            {
                throw new RuntimeException('ask() not expected: the non-2xx status branch returns before the stream closure.');
            }

            public function markUnavailable(): void
            {
                // Mirror RagClient::markUnavailable so the test can prove the
                // controller invoked the cooldown via its observable side effect.
                Cache::put('rag:unavailable_until', now()->addSeconds(120)->timestamp, 120);
            }

            public function cancel(ResolvedAskRequest $request): void {}
        };

        $this->app->instance(RagClientInterface::class, $fake);

        $response = $this->actingAs($this->user)
            ->postJson('/ai-search/stream', ['question' => 'will upstream fail?']);

        $response->assertStatus(503);
        $response->assertJson(['message' => 'AI search temporarily unavailable.']);
        $this->assertStringNotContainsString(
            'text/event-stream',
            (string) $response->headers->get('Content-Type')
        );
        $this->assertNotNull(Cache::get('rag:unavailable_until'));
    }

    /**
     * Upstream throw: controller catches, returns 503 JSON and marks unavailable.
     *
     * @test
     */
    public function upstream_exception_returns_503_json_and_marks_unavailable(): void
    {
        $fake = new class extends RagClientStub
        {
            public function stream(ResolvedAskRequest $request): ResponseInterface
            {
                throw new RuntimeException('connection refused');
            }

            public function ask(ResolvedAskRequest $request): array
            {
                throw new RuntimeException('ask() not expected: the upstream-throw branch returns before the stream closure.');
            }

            public function markUnavailable(): void
            {
                // Mirror RagClient::markUnavailable so the test can prove the
                // controller invoked the cooldown via its observable side effect.
                Cache::put('rag:unavailable_until', now()->addSeconds(120)->timestamp, 120);
            }

            public function cancel(ResolvedAskRequest $request): void {}
        };

        $this->app->instance(RagClientInterface::class, $fake);

        $response = $this->actingAs($this->user)
            ->postJson('/ai-search/stream', ['question' => 'will upstream throw?']);

        $response->assertStatus(503);
        $response->assertJson(['message' => 'AI search temporarily unavailable.']);
        $this->assertNotNull(Cache::get('rag:unavailable_until'));
    }

    /**
     * Database-question fallback: upstream yields the non_streamable_query_type
     * error frame, the controller suppresses it, re-asks via ask() and re-emits
     * the JSON answer as synthetic SSE. Proven via the billing side effect — the
     * synthetic token frames flowed through the observer and charged char-based.
     *
     * @test
     */
    public function structured_fallback_re_emits_json_answer_and_charges_char_based(): void
    {
        $fake = new class extends RagClientStub
        {
            public function stream(ResolvedAskRequest $request): ResponseInterface
            {
                return new Psr7Response(
                    200,
                    ['Content-Type' => 'text/event-stream'],
                    Utils::streamFor(
                        "event: error\ndata: {\"error\":\"non_streamable_query_type\",\"detail\":\"...\"}\n\n"
                    )
                );
            }

            public function ask(ResolvedAskRequest $request): array
            {
                return [
                    'question' => $request->question,
                    'answer' => 'See [Source 2] for the structured answer.',
                    'sources' => [[
                        'content' => 'Authorized fallback source.',
                        'source_file' => 'fallback-policy.md',
                        'chunk_index' => 0,
                    ]],
                    'model' => 'gpt-4o-mini',
                    'query_type' => 'structured',
                ];
            }

            public function cancel(ResolvedAskRequest $request): void {}
        };

        $this->app->instance(RagClientInterface::class, $fake);

        $response = $this->actingAs($this->user)
            ->post('/ai-search/stream', ['question' => 'What was May revenue?']);

        $response->assertOk();
        $this->assertStringContainsString('text/event-stream', (string) $response->headers->get('Content-Type'));

        $output = $this->capturedStreamOutput($response);

        // Synthetic token frames passed through the observer → char-based charge.
        $this->assertGreaterThan(0, app(AiCreditsService::class)->currentUsage($this->user));
        $this->assertStringContainsString('event: citations', $output);
        $this->assertStringContainsString('"parsed":false', $output);
        $this->assertStringNotContainsString('[Source 2]', $output);
    }

    /**
     * Fallback ask() failure: the controller marks the service unavailable and
     * emits an error frame. Proven via the markUnavailable() cooldown side effect.
     *
     * @test
     */
    public function fallback_emits_error_frame_when_ask_throws_without_marking_service_unavailable(): void
    {
        $fake = new class extends RagClientStub
        {
            public function stream(ResolvedAskRequest $request): ResponseInterface
            {
                return new Psr7Response(
                    200,
                    ['Content-Type' => 'text/event-stream'],
                    Utils::streamFor(
                        "event: error\ndata: {\"error\":\"non_streamable_query_type\",\"detail\":\"...\"}\n\n"
                    )
                );
            }

            public function ask(ResolvedAskRequest $request): array
            {
                throw new RuntimeException('json ask failed');
            }

            public function markUnavailable(): void
            {
                Cache::put('rag:unavailable_until', now()->addSeconds(120)->timestamp, 120);
            }

            public function cancel(ResolvedAskRequest $request): void {}
        };

        $this->app->instance(RagClientInterface::class, $fake);

        $response = $this->actingAs($this->user)
            ->post('/ai-search/stream', ['question' => 'count invoices last quarter']);

        $response->assertOk();
        $output = $this->capturedStreamOutput($response);

        $this->assertStringContainsString('event: error', $output);
        $this->assertNull(Cache::get('rag:unavailable_until'));
    }

    /**
     * T-0.4: the JSON-fallback failure leg — emitJsonFallback's one caller
     * fires only when streaming itself failed non_streamable_query_type —
     * must still carry the correlation ID, same as the other legs.
     *
     * @test
     */
    public function it_logs_the_correlation_id_when_the_json_fallback_ask_throws(): void
    {
        $runId = str_repeat('f', 32);
        $fake = new class extends RagClientStub
        {
            public function stream(ResolvedAskRequest $request): ResponseInterface
            {
                return new Psr7Response(
                    200,
                    ['Content-Type' => 'text/event-stream'],
                    Utils::streamFor(
                        "event: error\ndata: {\"error\":\"non_streamable_query_type\",\"detail\":\"...\"}\n\n"
                    )
                );
            }

            public function ask(ResolvedAskRequest $request): array
            {
                throw new RuntimeException('json ask failed');
            }

            public function cancel(ResolvedAskRequest $request): void {}
        };
        $this->app->instance(RagClientInterface::class, $fake);

        $spy = Log::spy();
        $spy->shouldReceive('channel')->andReturnSelf();

        $response = $this->actingAs($this->user)
            ->post('/ai-search/stream', ['question' => 'count invoices last quarter', 'run_id' => $runId]);

        $response->assertOk();
        $this->capturedStreamOutput($response);

        $spy->shouldHaveReceived('error')->withArgs(function ($message, $context = []) use ($runId) {
            return $message === 'ai-search fallback failed'
                && is_array($context)
                && ($context['correlation_id'] ?? null) === $runId;
        });
    }

    /** @test */
    public function it_returns_422_when_question_is_missing(): void
    {
        $this->actingAs($this->user)
            ->postJson('/ai-search/stream', [])
            ->assertStatus(422);
    }

    /** @test */
    public function it_returns_422_when_question_is_below_min_length(): void
    {
        $this->actingAs($this->user)
            ->postJson('/ai-search/stream', ['question' => 'a'])
            ->assertStatus(422);
    }

    /** @test */
    public function it_returns_422_when_question_exceeds_max_length(): void
    {
        $this->actingAs($this->user)
            ->postJson('/ai-search/stream', ['question' => str_repeat('a', 2001)])
            ->assertStatus(422);
    }

    /**
     * Telemetry: the ask log is emitted on the dedicated 'ai-search' channel.
     *
     * @test
     */
    public function it_logs_the_ask_on_the_ai_search_channel(): void
    {
        $body = "event: token\ndata: {\"d\":\"Hello\"}\n\n".
            "event: done\ndata: {\"model\":\"m\",\"usage\":{\"input_tokens\":1,\"output_tokens\":2}}\n\n";

        $this->app->instance(RagClientInterface::class, new FakeRagStreamClient($body));

        // Under a bare Log::spy(), Log::channel() returns null and the closure's
        // ->info() call fatals; stub channel() to return the spy itself so the
        // logger chain resolves and the interaction is recorded.
        $spy = Log::spy();
        $spy->shouldReceive('channel')->andReturnSelf();

        $response = $this->actingAs($this->user)
            ->post('/ai-search/stream', ['question' => 'telemetry?']);

        $response->assertOk();
        // The ask log is written inside the StreamedResponse closure.
        $this->runStream($response);

        $spy->shouldHaveReceived('channel')->with('ai-search');
        $spy->shouldHaveReceived('info')->withArgs(function ($message, $context = []) {
            return $message === 'ai-search ask'
                && is_array($context)
                && array_key_exists('operation', $context)
                && array_key_exists('outcome', $context)
                && array_key_exists('usage', $context);
        });
    }

    /**
     * T-0.4: the 'ai-search ask' success log carries the correlation ID
     * (today it logs nothing).
     *
     * @test
     */
    public function it_logs_the_correlation_id_on_the_ask_log(): void
    {
        $runId = str_repeat('d', 32);
        $body = "event: token\ndata: {\"d\":\"Hello\"}\n\n".
            "event: done\ndata: {\"model\":\"m\"}\n\n";

        $this->app->instance(RagClientInterface::class, new FakeRagStreamClient($body));

        $spy = Log::spy();
        $spy->shouldReceive('channel')->andReturnSelf();

        $response = $this->actingAs($this->user)
            ->post('/ai-search/stream', ['question' => 'telemetry?', 'run_id' => $runId]);

        $response->assertOk();
        $this->runStream($response);

        $spy->shouldHaveReceived('info')->withArgs(function ($message, $context = []) use ($runId) {
            return $message === 'ai-search ask'
                && is_array($context)
                && ($context['correlation_id'] ?? null) === $runId;
        });
    }

    /**
     * T-0.4: an early upstream failure (before the SSE loop even starts)
     * must still carry the correlation ID.
     *
     * @test
     */
    public function it_logs_the_correlation_id_when_the_upstream_call_throws(): void
    {
        $runId = str_repeat('e', 32);
        $fake = new class extends RagClientStub
        {
            public function stream(ResolvedAskRequest $request): ResponseInterface
            {
                throw new RuntimeException('connection refused');
            }

            public function cancel(ResolvedAskRequest $request): void {}
        };
        $this->app->instance(RagClientInterface::class, $fake);

        $spy = Log::spy();
        $spy->shouldReceive('channel')->andReturnSelf();

        $this->actingAs($this->user)
            ->postJson('/ai-search/stream', ['question' => 'will upstream throw?', 'run_id' => $runId])
            ->assertStatus(503);

        $spy->shouldHaveReceived('warning')->withArgs(function ($message, $context = []) use ($runId) {
            return $message === 'ai-search upstream failed'
                && is_array($context)
                && ($context['correlation_id'] ?? null) === $runId;
        });
    }

    /** @test */
    public function incomplete_upstream_stream_emits_error_frame(): void
    {
        $body = "event: token\ndata: {\"d\":\"partial answer\"}\n\n";

        $this->app->instance(RagClientInterface::class, new FakeRagStreamClient($body));

        $response = $this->actingAs($this->user)
            ->post('/ai-search/stream', ['question' => 'What is MCP?']);

        $response->assertOk();
        $output = $this->capturedStreamOutput($response);

        $this->assertStringContainsString('incomplete_stream', $output);
    }

    /** @test */
    public function it_validates_thread_id_format(): void
    {
        $this->actingAs($this->user)
            ->postJson('/ai-search/stream', [
                'question' => 'valid question',
                'thread_id' => str_repeat('z', 32),
            ])
            ->assertStatus(422)
            ->assertJsonValidationErrors(['thread_id']);
    }

    /**
     * T-0.2: a caller-supplied run_id must be rejected outright, never
     * silently regenerated — the caller would otherwise cancel with a
     * different ID than the one Billing actually used.
     *
     * @test
     */
    public function it_rejects_an_all_zero_run_id(): void
    {
        $this->actingAs($this->user)
            ->postJson('/ai-search/stream', [
                'question' => 'valid question',
                'run_id' => str_repeat('0', 32),
            ])
            ->assertStatus(422)
            ->assertJsonValidationErrors(['run_id']);
    }

    // T-0.2 / F7: the trailing-newline anchoring defect (PCRE `$` also
    // matches before a trailing newline) is covered at the unit layer in
    // Tests\Unit\AskAi\CorrelationIdTest, not here. The app's global
    // TrimStrings middleware trims a trailing "\n" off every string input
    // BEFORE validation runs, so an HTTP-level test never actually sends
    // the un-trimmed value to the validator — asserting 422 here would only
    // prove the middleware, not the regex anchor.

    /** @test */
    public function it_rejects_a_run_id_with_uppercase_hex_characters(): void
    {
        $this->actingAs($this->user)
            ->postJson('/ai-search/stream', [
                'question' => 'valid question',
                'run_id' => str_repeat('A', 32),
            ])
            ->assertStatus(422)
            ->assertJsonValidationErrors(['run_id']);
    }

    /** @test */
    public function it_accepts_a_well_formed_run_id(): void
    {
        $body = "event: token\ndata: {\"d\":\"Hello\"}\n\n".
            "event: done\ndata: {\"model\":\"test-model\"}\n\n";
        $this->app->instance(RagClientInterface::class, new FakeRagStreamClient($body));

        $this->actingAs($this->user)
            ->postJson('/ai-search/stream', [
                'question' => 'valid question',
                'run_id' => str_repeat('a', 32),
            ])
            ->assertOk();
    }

    /**
     * AC-0.11 / F25: when the caller sends no run_id, Billing's own fallback
     * must mint the same 32-lowercase-hex shape it validates — the previous
     * Str::random(32) fallback was base62, which RAG's stricter hex
     * validation would reject downstream once T-0.2's RAG half ships.
     *
     * @test
     */
    public function it_generates_a_valid_hex_run_id_when_the_caller_sends_none(): void
    {
        $body = "event: token\ndata: {\"d\":\"Hello\"}\n\n".
            "event: done\ndata: {\"model\":\"test-model\"}\n\n";
        $fake = new FakeRagStreamClient($body);
        $this->app->instance(RagClientInterface::class, $fake);

        $this->actingAs($this->user)
            ->post('/ai-search/stream', ['question' => 'What is MCP?'])
            ->assertOk();

        $this->assertNotNull($fake->lastStreamRequest);
        $this->assertMatchesRegularExpression('/\A[a-f0-9]{32}\z/', $fake->lastStreamRequest->runId);
        $this->assertNotSame(str_repeat('0', 32), $fake->lastStreamRequest->runId);
    }

    /** @test */
    public function streamed_done_thread_id_passes_through_verbatim(): void
    {
        $threadId = str_repeat('a', 32);
        $body = "event: token\ndata: {\"d\":\"Hello\"}\n\n".
            "event: done\ndata: {\"model\":\"test-model\",\"thread_id\":\"{$threadId}\"}\n\n";

        $this->app->instance(RagClientInterface::class, new FakeRagStreamClient($body));

        $response = $this->actingAs($this->user)
            ->post('/ai-search/stream', [
                'question' => 'What is MCP?',
                'thread_id' => $threadId,
            ]);

        $response->assertOk();
        $output = $this->capturedStreamOutput($response);
        $this->assertStringContainsString('"thread_id":"'.$threadId.'"', $output);
    }

    /** @test */
    public function json_fallback_done_includes_thread_id_from_answer(): void
    {
        $fake = new class extends RagClientStub
        {
            public function stream(ResolvedAskRequest $request): ResponseInterface
            {
                return new Psr7Response(
                    200,
                    ['Content-Type' => 'text/event-stream'],
                    Utils::streamFor(
                        "event: error\ndata: {\"error\":\"non_streamable_query_type\",\"detail\":\"...\"}\n\n"
                    )
                );
            }

            public function ask(ResolvedAskRequest $request): array
            {
                return [
                    'question' => $request->question,
                    'answer' => 'SQL answer here.',
                    'sources' => [],
                    'model' => 'gpt-4o-mini',
                    'thread_id' => str_repeat('a', 32),
                ];
            }

            public function cancel(ResolvedAskRequest $request): void {}
        };

        $this->app->instance(RagClientInterface::class, $fake);

        $response = $this->actingAs($this->user)
            ->post('/ai-search/stream', ['question' => 'How many invoices?']);

        $response->assertOk();
        $output = $this->capturedStreamOutput($response);
        $this->assertStringContainsString('"thread_id":"'.str_repeat('a', 32).'"', $output);
    }

    /**
     * Capture streamed SSE bytes while driving the closure to completion.
     */
    private function capturedStreamOutput(TestResponse $response): string
    {
        return CapturedStreamOutput::from($response);
    }

    /**
     * Drive a StreamedResponse's closure to completion (credits + telemetry run
     * inside it). Restores PHPUnit's output-buffer stack depth afterwards.
     */
    private function runStream(TestResponse $response): void
    {
        $target = ob_get_level();

        $response->baseResponse->sendContent();

        while (ob_get_level() < $target) {
            ob_start();
        }
        while (ob_get_level() > $target) {
            ob_end_clean();
        }
    }
}
