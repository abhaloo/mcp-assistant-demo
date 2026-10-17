<?php

declare(strict_types=1);

namespace Tests\Feature\Ai;

use App\Models\Entity;
use App\Models\User;
use App\Services\AskAi\ResolvedAskRequest;
use App\Services\RagClientInterface;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Log;
use RuntimeException;
use Tests\Support\FakeRagStreamClient;
use Tests\Support\RagClientStub;
use Tests\TestCase;

class AiSearchCancelTest extends TestCase
{
    use RefreshDatabase;

    protected User $user;

    private FakeRagStreamClient $rag;

    protected function setUp(): void
    {
        parent::setUp();

        Entity::factory()->create(['id' => 1]);
        $this->user = User::factory()->create(['entity_id' => 1, 'role' => 'admin']);

        config([
            'services.rag.enabled' => true,
            'services.rag.url' => 'http://rag.test',
            'services.rag.jwt_secret' => 'cancel-test-secret',
        ]);

        $this->rag = new FakeRagStreamClient('');
        $this->app->instance(RagClientInterface::class, $this->rag);
    }

    /** @test */
    public function it_accepts_a_well_formed_run_id(): void
    {
        $runId = str_repeat('a', 32);

        $this->actingAs($this->user)
            ->postJson('/ai-search/cancel', ['run_id' => $runId])
            ->assertOk()
            ->assertExactJson(['success' => true]);

        $this->assertSame($runId, $this->rag->lastCancelRequest?->runId);
    }

    /**
     * T-0.2: reject, do not silently regenerate — a caller sending a bad ID
     * must get an error, not a different ID than the one it will use to
     * cancel.
     *
     * @test
     */
    public function it_rejects_an_all_zero_run_id(): void
    {
        $this->actingAs($this->user)
            ->postJson('/ai-search/cancel', ['run_id' => str_repeat('0', 32)])
            ->assertStatus(422)
            ->assertJsonValidationErrors(['run_id']);

        $this->assertNull($this->rag->lastCancelRequest);
    }

    // T-0.2 / F7: the trailing-newline anchoring defect is covered at the
    // unit layer in Tests\Unit\AskAi\CorrelationIdTest, not here — the
    // app's global TrimStrings middleware trims a trailing "\n" off every
    // string input before validation runs, so an HTTP-level test never
    // actually exercises the un-trimmed value against the regex.

    /** @test */
    public function it_rejects_a_missing_run_id(): void
    {
        $this->actingAs($this->user)
            ->postJson('/ai-search/cancel', [])
            ->assertStatus(422)
            ->assertJsonValidationErrors(['run_id']);
    }

    /**
     * T-0.4: the correlation ID is logged on the success leg too, not only
     * on failure.
     *
     * @test
     */
    public function it_logs_the_correlation_id_on_success(): void
    {
        $runId = str_repeat('b', 32);

        $spy = Log::spy();
        $spy->shouldReceive('channel')->andReturnSelf();

        $this->actingAs($this->user)
            ->postJson('/ai-search/cancel', ['run_id' => $runId])
            ->assertOk();

        $spy->shouldHaveReceived('channel')->with('ai-search');
        $spy->shouldHaveReceived('info')->withArgs(function ($message, $context = []) use ($runId) {
            return $message === 'ai-search cancel'
                && is_array($context)
                && ($context['correlation_id'] ?? null) === $runId;
        });
    }

    /**
     * T-0.4: the failure leg must still carry the correlation ID (today it
     * logs a warning with no ID at all).
     *
     * @test
     */
    public function it_logs_the_correlation_id_on_failure(): void
    {
        $runId = str_repeat('c', 32);

        $failing = new class extends RagClientStub
        {
            public function cancel(ResolvedAskRequest $request): void
            {
                throw new RuntimeException('upstream cancel failed');
            }
        };
        $this->app->instance(RagClientInterface::class, $failing);

        $spy = Log::spy();
        $spy->shouldReceive('channel')->andReturnSelf();

        $this->actingAs($this->user)
            ->postJson('/ai-search/cancel', ['run_id' => $runId])
            ->assertStatus(502);

        $spy->shouldHaveReceived('channel')->with('ai-search');
        $spy->shouldHaveReceived('warning')->withArgs(function ($message, $context = []) use ($runId) {
            return $message === 'ai-search cancel failed'
                && is_array($context)
                && ($context['correlation_id'] ?? null) === $runId;
        });
    }
}
