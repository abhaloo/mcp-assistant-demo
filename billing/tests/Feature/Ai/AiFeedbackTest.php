<?php

declare(strict_types=1);

namespace Tests\Feature\Ai;

use App\Models\Entity;
use App\Models\User;
use App\Services\RagClientInterface;
use GuzzleHttp\Psr7\Response as Psr7Response;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Http\Client\ConnectionException;
use Illuminate\Http\Client\RequestException;
use Illuminate\Http\Client\Response;
use Illuminate\Support\Facades\Log;
use Tests\Support\FakeRagStreamClient;
use Tests\Support\RagClientStub;
use Tests\TestCase;

class AiFeedbackTest extends TestCase
{
    use RefreshDatabase;

    protected User $user;

    private FakeRagStreamClient $rag;

    private string $traceId;

    protected function setUp(): void
    {

        parent::setUp();

        Entity::factory()->create(['id' => 1]);

        $this->user = User::factory()->create(['entity_id' => 1, 'role' => 'user']);

        $this->traceId = str_repeat('a', 32);

        config([

            'services.rag.enabled' => true,

            'services.rag.url' => 'http://rag.test',

            'services.rag.jwt_secret' => 'feedback-test-secret',

        ]);

        // Isolate throttle state between tests so the 21st-request 429 test is deterministic.

        $this->app['cache']->flush();

        $this->rag = new FakeRagStreamClient('');

        $this->app->instance(RagClientInterface::class, $this->rag);

    }

    /** @return array<string, string> */
    private function payload(): array
    {

        return [

            'trace_id' => $this->traceId,

            'feedback_token' => 'signed-feedback-token',

            'verdict' => 'up',

        ];

    }

    /** @test */
    public function it_accepts_a_valid_feedback_payload(): void
    {

        $response = $this->actingAs($this->user)->postJson('/ai-search/feedback', [

            ...$this->payload(),

            'comment' => 'expected X',

        ]);

        $response->assertOk();

        $response->assertExactJson(['success' => true]);

        $this->assertSame($this->traceId, $this->rag->lastFeedbackPayload['trace_id']);

        $this->assertSame('signed-feedback-token', $this->rag->lastFeedbackPayload['feedback_token']);

        $this->assertSame('up', $this->rag->lastFeedbackPayload['verdict']);

        $this->assertSame('expected X', $this->rag->lastFeedbackPayload['comment']);

    }

    /** @test */
    public function it_forwards_a_bounded_down_reason_without_query_identity(): void
    {

        $this->actingAs($this->user)->postJson('/ai-search/feedback', [

            ...$this->payload(),

            'verdict' => 'down',

            'reason' => 'incorrect',

        ])->assertOk();

        $this->assertSame('incorrect', $this->rag->lastFeedbackPayload['reason']);

        $this->assertArrayNotHasKey('query_hash', $this->rag->lastFeedbackPayload);

        $this->assertArrayNotHasKey('question', $this->rag->lastFeedbackPayload);

    }

    /** @test */
    public function it_rejects_missing_trace_identity(): void
    {

        $this->actingAs($this->user)->postJson('/ai-search/feedback', [

            'verdict' => 'up',

        ])->assertStatus(422)->assertJsonValidationErrors(['trace_id', 'feedback_token']);

    }

    /** @test */
    public function it_rejects_an_invalid_trace_id(): void
    {

        $this->actingAs($this->user)->postJson('/ai-search/feedback', [

            ...$this->payload(),

            'trace_id' => 'trace-123',

        ])->assertStatus(422)->assertJsonValidationErrors(['trace_id']);

    }

    /** @test */
    public function it_rejects_an_all_zero_trace_id(): void
    {

        $this->actingAs($this->user)->postJson('/ai-search/feedback', [

            ...$this->payload(),

            'trace_id' => str_repeat('0', 32),

        ])->assertStatus(422)->assertJsonValidationErrors(['trace_id']);

    }

    /** @test */
    public function it_rejects_unknown_verdicts_and_reasons(): void
    {

        $this->actingAs($this->user)->postJson('/ai-search/feedback', [

            ...$this->payload(),

            'verdict' => 'maybe',

            'reason' => 'freeform',

        ])->assertStatus(422)->assertJsonValidationErrors(['verdict', 'reason']);

    }

    /** @test */
    public function it_rejects_a_comment_over_500_chars(): void
    {

        $this->actingAs($this->user)->postJson('/ai-search/feedback', [

            ...$this->payload(),

            'comment' => str_repeat('x', 501),

        ])->assertStatus(422)->assertJsonValidationErrors('comment');

    }

    /**
     * T-0.4: Billing logs the correlation ID on the feedback leg's success

     * path too, not only on failure. Billing proxies `trace_id` verbatim,

     * so it carries whatever the browser echoes back — the same

     * correlation ID the stream/cancel legs use.

     *

     * @test
     */
    public function it_logs_the_correlation_id_on_success(): void
    {

        $spy = Log::spy();

        $spy->shouldReceive('channel')->andReturnSelf();

        $this->actingAs($this->user)->postJson('/ai-search/feedback', $this->payload())

            ->assertOk();

        $spy->shouldHaveReceived('channel')->with('ai-search');

        $spy->shouldHaveReceived('info')->withArgs(function ($message, $context = []) {

            return $message === 'ai-search feedback'

                && is_array($context)

                && ($context['correlation_id'] ?? null) === $this->traceId;

        });

    }

    /**
     * T-0.4: the failure leg must carry the correlation ID too (today it

     * logs a warning with no ID at all).

     *

     * @test
     */
    public function it_logs_the_correlation_id_on_failure(): void
    {

        $failing = new class extends RagClientStub
        {
            public function feedback(array $payload, User $user): array
            {

                throw new RequestException(new Response(new Psr7Response(502, [], 'downstream failed')));
            }
        };

        $this->app->instance(RagClientInterface::class, $failing);

        $spy = Log::spy();

        $spy->shouldReceive('channel')->andReturnSelf();

        $this->actingAs($this->user)->postJson('/ai-search/feedback', $this->payload())

            ->assertStatus(502);

        $spy->shouldHaveReceived('channel')->with('ai-search');

        $spy->shouldHaveReceived('warning')->withArgs(function ($message, $context = []) {

            return $message === 'ai-search feedback proxy failed'

                && is_array($context)

                && ($context['correlation_id'] ?? null) === $this->traceId

                && array_key_exists('status', $context);

        });

    }

    /** @test */
    public function it_returns_502_when_the_upstream_connection_fails(): void
    {

        $failing = new class extends RagClientStub
        {
            public function feedback(array $payload, User $user): array
            {

                throw new ConnectionException('connection refused');
            }
        };

        $this->app->instance(RagClientInterface::class, $failing);

        $spy = Log::spy();

        $spy->shouldReceive('channel')->andReturnSelf();

        $this->actingAs($this->user)->postJson('/ai-search/feedback', $this->payload())

            ->assertStatus(502)

            ->assertJson(['message' => 'Feedback failed.']);

        $spy->shouldHaveReceived('warning')->withArgs(function ($message, $context = []) {

            return $message === 'ai-search feedback proxy failed'

                && is_array($context)

                && ($context['correlation_id'] ?? null) === $this->traceId

                && array_key_exists('status', $context)

                && $context['status'] === null;

        });

    }

    /** @test */
    public function feedback_route_is_throttled_at_20_per_minute(): void
    {

        // Locks C2: the route carries throttle:20,1. The 21st request must be 429.

        $this->actingAs($this->user);

        for ($i = 0; $i < 20; $i++) {

            $this->postJson('/ai-search/feedback', [

                ...$this->payload(),

            ])->assertOk();

        }

        $this->postJson('/ai-search/feedback', [

            ...$this->payload(),

        ])->assertStatus(429);

    }
}
