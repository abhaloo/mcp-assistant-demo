<?php

declare(strict_types=1);

namespace Tests\Feature\AskAi;

use App\Models\Entity;
use App\Models\User;
use App\Services\AiCreditsService;
use App\Services\RagClientInterface;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Cache;
use Illuminate\Testing\TestResponse;
use Tests\Support\CapturedStreamOutput;
use Tests\Support\FakeRagStreamClient;
use Tests\TestCase;

/**
 * streamV2 charges the per-user credits cap from RAG's priced per-turn figure
 * on the outcome frame, behind services.rag.credits.charge_v2. The upstream
 * is faked with a canned SSE body; a stream cut before its turn_outcome stops
 * before the marker and charges nothing.
 */
final class StreamV2CreditsChargeTest extends TestCase
{
    use RefreshDatabase;

    private AiCreditsService $credits;

    private User $user;

    protected function setUp(): void
    {
        parent::setUp();

        Cache::flush();

        Entity::factory()->create(['id' => 1]);
        $this->user = User::factory()->create(['entity_id' => 1, 'role' => 'user']);

        config([
            'services.rag.enabled' => true,
            'services.rag.url' => 'http://rag.test',
            'services.rag.jwt_secret' => 'charge-test-secret',
            'ask_ai.ask_v2_enabled' => true,
        ]);

        $this->credits = new AiCreditsService;
    }

    /** @return array<string, mixed> */
    private function payload(): array
    {
        return [
            'protocol_version' => '2',
            'operation' => 'new_question',
            'run_id' => '00000000000000000000000000000001',
            'thread_id' => 'fixture-thread-1',
            'question' => 'Show items list',
            'idempotency_key' => 'idemp-1',
            'deadline_at_ms' => 1800000025000,
            'response_policy' => 'strict',
        ];
    }

    private function outcomeBody(string $budgetJson): string
    {
        return implode("\n", [
            'event: turn_outcome',
            'data: {"run_id":"r1","outcome_type":"answered","trusted":true,'
                .'"budget":'.$budgetJson.'}',
            '',
            '',
        ]);
    }

    private function capturedStreamOutput(TestResponse $response): string
    {
        return CapturedStreamOutput::from($response);
    }

    private function stream(string $body): void
    {
        $this->app->instance(RagClientInterface::class, new FakeRagStreamClient($body));
        $response = $this->actingAs($this->user)->postJson('/api/ai/ask/v2', $this->payload());
        $this->capturedStreamOutput($response);
    }

    /** @test */
    public function a_charged_turn_moves_the_counter_by_rags_priced_figure(): void
    {
        config(['services.rag.credits.charge_v2' => true]);

        $this->stream($this->outcomeBody(
            '{"this_turn_usd":"0.02","used_usd":"1.10","limit_usd":"10.00","period_end":"2026-09-30","reset_at":"2026-10-01T00:00:00Z"}'
        ));

        $this->assertEqualsWithDelta(0.02, $this->credits->currentUsage($this->user), 1e-9);
    }

    /** @test */
    public function a_stream_without_a_budget_frame_charges_nothing(): void
    {
        config(['services.rag.credits.charge_v2' => true]);

        $this->stream("event: turn_outcome\ndata: {\"run_id\":\"r1\",\"outcome_type\":\"answered\"}\n\n");

        $this->assertEqualsWithDelta(0.0, $this->credits->currentUsage($this->user), 1e-9);
    }

    /** @test */
    public function a_stream_cut_before_the_terminal_marker_charges_nothing(): void
    {
        config(['services.rag.credits.charge_v2' => true]);

        $this->stream("event: activity\ndata: {\"activity_id\":\"a1\"}\n\n");

        $this->assertEqualsWithDelta(0.0, $this->credits->currentUsage($this->user), 1e-9);
    }

    /** @test */
    public function with_charge_v2_off_nothing_moves(): void
    {
        config(['services.rag.credits.charge_v2' => false]);

        $this->stream($this->outcomeBody(
            '{"this_turn_usd":"0.02","used_usd":"1.10","limit_usd":"10.00","period_end":"2026-09-30","reset_at":"2026-10-01T00:00:00Z"}'
        ));

        $this->assertEqualsWithDelta(0.0, $this->credits->currentUsage($this->user), 1e-9);
    }

    /** @test */
    public function an_unpriced_turn_charges_nothing(): void
    {
        config(['services.rag.credits.charge_v2' => true]);

        $this->stream($this->outcomeBody('{"this_turn_usd":null,"used_usd":"1.10","limit_usd":"10.00"}'));

        $this->assertEqualsWithDelta(0.0, $this->credits->currentUsage($this->user), 1e-9);
    }
}
