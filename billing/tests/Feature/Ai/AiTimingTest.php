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

class AiTimingTest extends TestCase
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
            'services.rag.jwt_secret' => 'timing-test-secret',
        ]);

        $this->app['cache']->flush();
        $this->rag = new FakeRagStreamClient('');
        $this->app->instance(RagClientInterface::class, $this->rag);
    }

    /** @return array<string, int|string> */
    private function payload(): array
    {
        return [
            'trace_id' => $this->traceId,
            'feedback_token' => 'signed-feedback-token',
            'ui_first_text_ms' => 120,
            'completion_latency_ms' => 450,
        ];
    }

    /** @test */
    public function it_requires_authentication(): void
    {
        $this->postJson('/ai-search/timing', $this->payload())
            ->assertStatus(401);
    }

    /** @test */
    public function it_accepts_a_valid_timing_payload(): void
    {
        $response = $this->actingAs($this->user)->postJson('/ai-search/timing', $this->payload());

        $response->assertOk();
        $response->assertExactJson(['success' => true]);
        $this->assertSame($this->traceId, $this->rag->lastTimingPayload['trace_id']);
        $this->assertSame('signed-feedback-token', $this->rag->lastTimingPayload['feedback_token']);
        $this->assertSame(120, $this->rag->lastTimingPayload['ui_first_text_ms']);
        $this->assertSame(450, $this->rag->lastTimingPayload['completion_latency_ms']);
        $this->assertSame($this->user->id, $this->rag->lastTimingUser?->id);
    }

    /** @test */
    public function it_rejects_missing_required_fields(): void
    {
        $this->actingAs($this->user)->postJson('/ai-search/timing', [
            'trace_id' => $this->traceId,
        ])->assertStatus(422)->assertJsonValidationErrors([
            'feedback_token',
            'ui_first_text_ms',
            'completion_latency_ms',
        ]);
    }

    /** @test */
    public function it_rejects_an_invalid_trace_id(): void
    {
        $this->actingAs($this->user)->postJson('/ai-search/timing', [
            ...$this->payload(),
            'trace_id' => 'trace-123',
        ])->assertStatus(422)->assertJsonValidationErrors(['trace_id']);
    }

    /** @test */
    public function it_rejects_an_all_zero_trace_id(): void
    {
        $this->actingAs($this->user)->postJson('/ai-search/timing', [
            ...$this->payload(),
            'trace_id' => str_repeat('0', 32),
        ])->assertStatus(422)->assertJsonValidationErrors(['trace_id']);
    }

    /** @test */
    public function it_logs_the_correlation_id_on_success(): void
    {
        $spy = Log::spy();
        $spy->shouldReceive('channel')->andReturnSelf();

        $this->actingAs($this->user)->postJson('/ai-search/timing', $this->payload())
            ->assertOk();

        $spy->shouldHaveReceived('channel')->with('ai-search');
        $spy->shouldHaveReceived('info')->withArgs(function ($message, $context = []) {
            return $message === 'ai-search timing'
                && is_array($context)
                && ($context['correlation_id'] ?? null) === $this->traceId;
        });
    }

    /** @test */
    public function it_logs_the_correlation_id_on_failure(): void
    {
        $failing = new class extends RagClientStub
        {
            public function timing(array $payload, User $user): array
            {
                throw new RequestException(new Response(new Psr7Response(502, [], 'downstream failed')));
            }
        };
        $this->app->instance(RagClientInterface::class, $failing);

        $spy = Log::spy();
        $spy->shouldReceive('channel')->andReturnSelf();

        $this->actingAs($this->user)->postJson('/ai-search/timing', $this->payload())
            ->assertStatus(502);

        $spy->shouldHaveReceived('channel')->with('ai-search');
        $spy->shouldHaveReceived('warning')->withArgs(function ($message, $context = []) {
            return $message === 'ai-search timing proxy failed'
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
            public function timing(array $payload, User $user): array
            {
                throw new ConnectionException('connection refused');
            }
        };
        $this->app->instance(RagClientInterface::class, $failing);

        $spy = Log::spy();
        $spy->shouldReceive('channel')->andReturnSelf();

        $this->actingAs($this->user)->postJson('/ai-search/timing', $this->payload())
            ->assertStatus(502)
            ->assertJson(['message' => 'Timing failed.']);

        $spy->shouldHaveReceived('warning')->withArgs(function ($message, $context = []) {
            return $message === 'ai-search timing proxy failed'
                && is_array($context)
                && ($context['correlation_id'] ?? null) === $this->traceId
                && array_key_exists('status', $context)
                && $context['status'] === null;
        });
    }

    /** @test */
    public function timing_route_is_throttled_at_20_per_minute(): void
    {
        $this->actingAs($this->user);

        for ($i = 0; $i < 20; $i++) {
            $this->postJson('/ai-search/timing', $this->payload())->assertOk();
        }

        $this->postJson('/ai-search/timing', $this->payload())->assertStatus(429);
    }
}
