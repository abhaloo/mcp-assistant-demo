<?php

declare(strict_types=1);

namespace Tests\Feature\Ai;

use App\Models\Entity;
use App\Models\User;
use App\Services\RagClientInterface;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Cache;
use Symfony\Component\HttpFoundation\StreamedResponse;
use Tests\Support\CapturedStreamOutput;
use Tests\Support\FakeRagStreamClient;
use Tests\TestCase;

final class AskV2StreamEnvelopeTest extends TestCase
{
    use RefreshDatabase;

    protected User $user;

    protected function setUp(): void
    {
        parent::setUp();

        Cache::flush();

        Entity::factory()->create(['id' => 1]);
        $this->user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);

        config([
            'services.rag.enabled' => true,
            'services.rag.url' => 'http://rag.test',
            'services.rag.jwt_secret' => 'stream-test-secret',
            'ask_ai.ask_v2_enabled' => true,
        ]);
    }

    public function test_card_shaped_clarification_reply_is_accepted(): void
    {
        $turnOutcomeBody = implode("\n", [
            'event: turn_outcome',
            'data: {"outcome_type":"answered","trusted":true}',
            '',
            '',
        ]);
        $fakeClient = new FakeRagStreamClient($turnOutcomeBody);
        $this->app->instance(RagClientInterface::class, $fakeClient);

        $payload = [
            'protocol_version' => '2',
            'operation' => 'clarification_reply',
            'run_id' => str_repeat('a', 32),
            'thread_id' => 'th-1',
            'idempotency_key' => 'idemp-1',
            'continuation_ref' => 'ticket.abc',
            'clarification_choice_id' => 'jobs_count',
            'response_policy' => 'strict',
        ];

        $response = $this->actingAs($this->user)
            ->postJson('/api/ai/ask/v2', $payload);

        $response->assertOk();
        $this->assertStringContainsString('text/event-stream', (string) $response->headers->get('Content-Type'));
        $this->assertStringContainsString('no-cache', (string) $response->headers->get('Cache-Control'));
        $response->assertHeader('X-Accel-Buffering', 'no');
        $this->assertInstanceOf(StreamedResponse::class, $response->baseResponse);

        $forwarded = $fakeClient->lastStreamV2Payload;
        $this->assertNotNull($forwarded);
        $this->assertSame('clarification_reply', $forwarded['operation']);
        $this->assertSame('ticket.abc', $forwarded['continuation_ref']);
        $this->assertSame('jobs_count', $forwarded['clarification_choice_id']);
        $this->assertSame('th-1', $forwarded['thread_id']);
        $this->assertSame(str_repeat('a', 32), $forwarded['run_id']);

        $output = CapturedStreamOutput::from($response);
        $this->assertStringContainsString('event: turn_outcome', $output);
    }

    public function test_free_text_clarification_reply_is_accepted(): void
    {
        $turnOutcomeBody = implode("\n", [
            'event: turn_outcome',
            'data: {"outcome_type":"answered","trusted":true}',
            '',
            '',
        ]);
        $fakeClient = new FakeRagStreamClient($turnOutcomeBody);
        $this->app->instance(RagClientInterface::class, $fakeClient);

        $payload = [
            'protocol_version' => '2',
            'operation' => 'clarification_reply',
            'run_id' => str_repeat('a', 32),
            'thread_id' => 'th-1',
            'idempotency_key' => 'idemp-2',
            'continuation_ref' => 'ticket.abc',
            'clarification_free_text' => 'Include unpaid invoices only',
            'response_policy' => 'strict',
        ];

        $response = $this->actingAs($this->user)
            ->postJson('/api/ai/ask/v2', $payload);

        $response->assertOk();
        $forwarded = $fakeClient->lastStreamV2Payload;
        $this->assertNotNull($forwarded);
        $this->assertSame('Include unpaid invoices only', $forwarded['clarification_free_text']);
    }

    public function test_clarification_reply_missing_continuation_ref_is_rejected(): void
    {
        $payload = [
            'protocol_version' => '2',
            'operation' => 'clarification_reply',
            'run_id' => str_repeat('a', 32),
            'thread_id' => 'th-1',
            'idempotency_key' => 'idemp-1',
            'continuation_ref' => null,
            'clarification_choice_id' => 'jobs_count',
            'response_policy' => 'strict',
        ];

        $this->actingAs($this->user)
            ->postJson('/api/ai/ask/v2', $payload)
            ->assertStatus(422)
            ->assertJsonValidationErrors(['continuation_ref']);
    }

    public function test_clarification_reply_with_both_choice_and_free_text_is_rejected(): void
    {
        $payload = [
            'protocol_version' => '2',
            'operation' => 'clarification_reply',
            'run_id' => str_repeat('a', 32),
            'thread_id' => 'th-1',
            'idempotency_key' => 'idemp-1',
            'continuation_ref' => 'ticket.abc',
            'clarification_choice_id' => 'jobs_count',
            'clarification_free_text' => 'all jobs',
            'response_policy' => 'strict',
        ];

        $this->actingAs($this->user)
            ->postJson('/api/ai/ask/v2', $payload)
            ->assertStatus(422)
            ->assertJsonValidationErrors(['clarification_choice_id']);
    }

    public function test_clarification_reply_with_neither_choice_nor_free_text_is_rejected(): void
    {
        $payload = [
            'protocol_version' => '2',
            'operation' => 'clarification_reply',
            'run_id' => str_repeat('a', 32),
            'thread_id' => 'th-1',
            'idempotency_key' => 'idemp-1',
            'continuation_ref' => 'ticket.abc',
            'response_policy' => 'strict',
        ];

        $this->actingAs($this->user)
            ->postJson('/api/ai/ask/v2', $payload)
            ->assertStatus(422)
            ->assertJsonValidationErrors(['clarification_choice_id']);
    }

    public function test_clarification_reply_with_prohibited_mutation_is_rejected(): void
    {
        $payload = [
            'protocol_version' => '2',
            'operation' => 'clarification_reply',
            'run_id' => str_repeat('a', 32),
            'thread_id' => 'th-1',
            'idempotency_key' => 'idemp-1',
            'continuation_ref' => 'ticket.abc',
            'clarification_choice_id' => 'jobs_count',
            'action' => 'delete_job',
            'response_policy' => 'strict',
        ];

        $this->actingAs($this->user)
            ->postJson('/api/ai/ask/v2', $payload)
            ->assertStatus(422)
            ->assertJsonValidationErrors(['action']);
    }
}
