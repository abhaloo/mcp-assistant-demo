<?php

declare(strict_types=1);

namespace Tests\Feature\Ai;

use App\Models\Entity;
use App\Models\User;
use App\Services\RagClientInterface;
use GuzzleHttp\Psr7\Response as Psr7Response;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Cache;
use Illuminate\Testing\TestResponse;
use Psr\Http\Message\ResponseInterface;
use RuntimeException;
use Symfony\Component\HttpFoundation\StreamedResponse;
use Tests\Support\CapturedStreamOutput;
use Tests\Support\FakeRagStreamClient;
use Tests\Support\RagClientStub;
use Tests\TestCase;

final class AiSearchV2StreamTest extends TestCase
{
    use RefreshDatabase;

    protected User $user;

    protected function setUp(): void
    {
        parent::setUp();

        Cache::flush();

        Entity::factory()->create(['id' => 1]);
        $this->user = User::factory()->create(['entity_id' => 1, 'role' => 'admin']);

        config([
            'services.rag.enabled' => true,
            'services.rag.url' => 'http://rag.test',
            'services.rag.jwt_secret' => 'stream-test-secret',
            'ask_ai.ask_v2_enabled' => true,
        ]);
    }

    /** @test */
    public function it_streams_upstream_v2_sse_events_with_correct_headers(): void
    {
        $sseBody = implode("\n", [
            'event: activity',
            'data: {"activity_id":"act-1","kind":"understand","state":"completed","elapsed_ms":10}',
            '',
            'event: table_start',
            'data: {"table_id":"table-1","columns":[{"key":"name","label":"Name"}]}',
            '',
            'event: table_rows',
            'data: {"table_id":"table-1","rows":[{"name":"Item A"}]}',
            '',
            'event: table_end',
            'data: {"table_id":"table-1","row_count":1,"table_digest":"digest-abc"}',
            '',
            'event: turn_outcome',
            'data: {"outcome_type":"answered","trusted":true}',
            '',
            '',
        ]);

        $this->app->instance(RagClientInterface::class, new FakeRagStreamClient($sseBody));

        $payload = [
            'protocol_version' => '2',
            'operation' => 'new_question',
            'run_id' => '00000000000000000000000000000001',
            'thread_id' => 'fixture-thread-1',
            'question' => 'Show items list',
            'idempotency_key' => 'idemp-1',
            'deadline_at_ms' => 1800000025000,
            'response_policy' => 'strict',
        ];

        $response = $this->actingAs($this->user)
            ->postJson('/api/ai/ask/v2', $payload);

        $response->assertOk();
        $this->assertStringContainsString('text/event-stream', (string) $response->headers->get('Content-Type'));
        $this->assertStringContainsString('no-cache', (string) $response->headers->get('Cache-Control'));
        $response->assertHeader('X-Accel-Buffering', 'no');
        $this->assertFalse($response->headers->has('Content-Length'));
        $this->assertInstanceOf(
            StreamedResponse::class,
            $response->baseResponse
        );

        $output = $this->capturedStreamOutput($response);
        $this->assertStringContainsString('event: activity', $output);
        $this->assertStringContainsString('event: table_start', $output);
        $this->assertStringContainsString('event: table_rows', $output);
        $this->assertStringContainsString('event: table_end', $output);
        $this->assertStringContainsString('event: turn_outcome', $output);
    }

    /** @test */
    public function it_opts_out_of_php_abort_on_write_while_streaming(): void
    {
        $this->app->instance(RagClientInterface::class, new FakeRagStreamClient("event: turn_outcome\ndata: {}\n\n"));

        $previous = ignore_user_abort(false);

        $response = $this->actingAs($this->user)->postJson('/api/ai/ask/v2', [
            'protocol_version' => '2',
            'operation' => 'new_question',
            'run_id' => '00000000000000000000000000000002',
            'thread_id' => 'fixture-thread-2',
            'question' => 'Show items list',
            'idempotency_key' => 'idemp-2',
            'deadline_at_ms' => 1800000025000,
            'response_policy' => 'strict',
        ]);
        $this->capturedStreamOutput($response);

        // The closure must opt out of PHP's abort-on-write so its own
        // connection_aborted() check and the post-loop telemetry run.
        $flagAfterStream = (bool) ignore_user_abort();
        ignore_user_abort((bool) $previous);
        $this->assertTrue($flagAfterStream);
    }

    /** @test */
    public function unauthenticated_v2_requests_are_rejected(): void
    {
        $payload = [
            'protocol_version' => '2',
            'operation' => 'new_question',
            'run_id' => '00000000000000000000000000000001',
            'thread_id' => 'fixture-thread-1',
            'question' => 'Test unauthenticated',
            'idempotency_key' => 'idemp-1',
            'deadline_at_ms' => 1800000025000,
            'response_policy' => 'strict',
        ];

        $response = $this->postJson('/api/ai/ask/v2', $payload);

        $response->assertStatus(401);
    }

    /** @test */
    public function it_returns_503_when_rag_is_disabled(): void
    {
        config(['services.rag.enabled' => false]);

        $payload = [
            'protocol_version' => '2',
            'operation' => 'new_question',
            'run_id' => '00000000000000000000000000000001',
            'thread_id' => 'fixture-thread-1',
            'question' => 'Test disabled',
            'idempotency_key' => 'idemp-1',
            'deadline_at_ms' => 1800000025000,
            'response_policy' => 'strict',
        ];

        $response = $this->actingAs($this->user)
            ->postJson('/api/ai/ask/v2', $payload);

        $response->assertStatus(503);
    }

    /** @test */
    public function it_validates_request_payload_with_v2_rules(): void
    {
        // Missing question on new_question
        $payload = [
            'protocol_version' => '2',
            'operation' => 'new_question',
            'run_id' => '00000000000000000000000000000001',
            'thread_id' => 'fixture-thread-1',
            'question' => null,
            'idempotency_key' => 'idemp-1',
            'deadline_at_ms' => 1800000025000,
            'response_policy' => 'strict',
        ];

        $this->actingAs($this->user)
            ->postJson('/api/ai/ask/v2', $payload)
            ->assertStatus(422)
            ->assertJsonValidationErrors(['question']);

        // Prohibited mutation field
        $invalidMutationPayload = [
            'protocol_version' => '2',
            'operation' => 'new_question',
            'run_id' => '00000000000000000000000000000001',
            'thread_id' => 'fixture-thread-1',
            'question' => 'Delete something',
            'idempotency_key' => 'idemp-1',
            'deadline_at_ms' => 1800000025000,
            'response_policy' => 'strict',
            'action' => 'delete_job',
        ];

        $this->actingAs($this->user)
            ->postJson('/api/ai/ask/v2', $invalidMutationPayload)
            ->assertStatus(422)
            ->assertJsonValidationErrors(['action']);
    }

    /** @test */
    public function upstream_non_2xx_returns_mapped_error_and_marks_unavailable(): void
    {
        $fake = new class extends RagClientStub
        {
            public function streamV2(array $payload, User $user): ResponseInterface
            {
                return new Psr7Response(503, [], 'upstream down');
            }

            public function markUnavailable(): void
            {
                Cache::put('rag:unavailable_until', now()->addSeconds(120)->timestamp, 120);
            }
        };

        $this->app->instance(RagClientInterface::class, $fake);

        $payload = [
            'protocol_version' => '2',
            'operation' => 'new_question',
            'run_id' => '00000000000000000000000000000001',
            'thread_id' => 'fixture-thread-1',
            'question' => 'Will upstream fail?',
            'idempotency_key' => 'idemp-1',
            'deadline_at_ms' => 1800000025000,
            'response_policy' => 'strict',
        ];

        $response = $this->actingAs($this->user)
            ->postJson('/api/ai/ask/v2', $payload);

        $response->assertStatus(503);
        $this->assertNotNull(Cache::get('rag:unavailable_until'));
    }

    /** @test */
    public function upstream_exception_returns_503_and_marks_unavailable(): void
    {
        $fake = new class extends RagClientStub
        {
            public function streamV2(array $payload, User $user): ResponseInterface
            {
                throw new RuntimeException('upstream connection refused');
            }

            public function markUnavailable(): void
            {
                Cache::put('rag:unavailable_until', now()->addSeconds(120)->timestamp, 120);
            }
        };

        $this->app->instance(RagClientInterface::class, $fake);

        $payload = [
            'protocol_version' => '2',
            'operation' => 'new_question',
            'run_id' => '00000000000000000000000000000001',
            'thread_id' => 'fixture-thread-1',
            'question' => 'Will upstream throw?',
            'idempotency_key' => 'idemp-1',
            'deadline_at_ms' => 1800000025000,
            'response_policy' => 'strict',
        ];

        $response = $this->actingAs($this->user)
            ->postJson('/api/ai/ask/v2', $payload);

        $response->assertStatus(503);
        $this->assertNotNull(Cache::get('rag:unavailable_until'));
    }

    /** @test */
    public function a_terminal_frame_longer_than_one_relay_read_is_forwarded_whole(): void
    {
        // Oracle: the relay reads the upstream in 8192-byte chunks; a turn_outcome
        // that carries twenty record links exceeds that, so the frame must be
        // forwarded up to its blank-line terminator, not cut at the chunk edge.
        $records = [];
        for ($i = 0; $i < 60; $i++) {
            $records[] = ['resource' => 'customer', 'record_id' => $i, 'label' => str_repeat('Customer '.$i.' ', 12), 'preview' => null];
        }
        $terminalData = json_encode(['outcome_type' => 'answered', 'trusted' => true, 'records' => $records]);
        $this->assertGreaterThan(8192, strlen($terminalData));
        $terminalFrame = "event: turn_outcome\ndata: {$terminalData}\n\n";
        $sseBody = "event: activity\ndata: {\"activity_id\":\"act-1\",\"kind\":\"understand\",\"state\":\"completed\",\"elapsed_ms\":10}\n\n".$terminalFrame;

        $this->app->instance(RagClientInterface::class, new FakeRagStreamClient($sseBody));

        $response = $this->actingAs($this->user)->postJson('/api/ai/ask/v2', [
            'protocol_version' => '2',
            'operation' => 'new_question',
            'run_id' => '00000000000000000000000000000002',
            'thread_id' => 'fixture-thread-2',
            'question' => 'Which customers are overdue',
            'idempotency_key' => 'idemp-2',
            'deadline_at_ms' => 1800000025000,
            'response_policy' => 'strict',
        ]);

        $response->assertOk();
        $output = $this->capturedStreamOutput($response);
        $this->assertStringContainsString($terminalFrame, $output);
    }

    private function capturedStreamOutput(TestResponse $response): string
    {
        return CapturedStreamOutput::from($response);
    }
}
