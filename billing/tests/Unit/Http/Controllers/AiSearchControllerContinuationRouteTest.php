<?php

declare(strict_types=1);

namespace Tests\Unit\Http\Controllers;

use App\Models\User;
use App\Services\RagClientInterface;
use Symfony\Component\HttpFoundation\StreamedResponse;
use Tests\Support\FakeRagStreamClient;
use Tests\TestCase;

class AiSearchControllerContinuationRouteTest extends TestCase
{
    /** @test */
    public function it_accepts_a_result_page_cursor_without_a_question_over_the_sse_route(): void
    {
        config(['services.rag.enabled' => true]);
        $rag = new FakeRagStreamClient("event: done\ndata: {\"model\":\"test-model\"}\n\n");
        $this->app->instance(RagClientInterface::class, $rag);

        // Keep this contract test independent of the malformed shared SQLite
        // test image: the controller and route validation do not need a DB.
        $this->withoutMiddleware();
        $response = $this->actingAs(new User)
            ->withHeaders(['Accept' => 'text/event-stream'])
            ->post('/ai-search/stream', [
                'operation' => 'ask',
                'result_page_cursor' => 'signed.cursor',
            ]);

        $response->assertOk();
        $this->assertInstanceOf(StreamedResponse::class, $response->baseResponse);
        $this->assertNotNull($rag->lastStreamRequest);
        $this->assertNull($rag->lastStreamRequest->question);
        $this->assertSame('signed.cursor', $rag->lastStreamRequest->resultPageCursor);
    }
}
