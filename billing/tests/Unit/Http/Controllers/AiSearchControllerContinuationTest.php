<?php

declare(strict_types=1);

namespace Tests\Unit\Http\Controllers;

use App\Http\Controllers\AiSearchController;
use App\Models\User;
use App\Services\AiCreditsService;
use App\Services\AskAi\PageContext\PageContextResolverRegistry;
use Illuminate\Http\Request;
use Illuminate\Validation\ValidationException;
use Tests\Support\FakeRagStreamClient;
use Tests\TestCase;

class AiSearchControllerContinuationTest extends TestCase
{
    /** @test */
    public function it_rejects_a_request_that_contains_neither_question_nor_result_page_cursor(): void
    {
        config(['services.rag.enabled' => true]);

        $request = Request::create('/ai-search/stream', 'POST', []);
        $request->setUserResolver(static fn (): User => new User);
        $controller = $this->controller(new FakeRagStreamClient);

        $response = $controller->stream($request);

        $this->assertSame(422, $response->getStatusCode());
        $this->assertSame('invalid_ask_request', $response->getData(true)['error']);
    }

    /** @test */
    public function it_rejects_a_request_that_contains_both_question_and_result_page_cursor(): void
    {
        config(['services.rag.enabled' => true]);

        $request = Request::create('/ai-search/stream', 'POST', [
            'question' => 'show jobs',
            'result_page_cursor' => 'signed.cursor',
        ]);
        $request->setUserResolver(static fn (): User => new User);

        $response = $this->controller(new FakeRagStreamClient)->stream($request);

        $this->assertSame(422, $response->getStatusCode());
        $this->assertSame('invalid_ask_request', $response->getData(true)['error']);
    }

    /** @test */
    public function it_forwards_only_the_opaque_result_page_cursor_to_rag(): void
    {
        config(['services.rag.enabled' => true]);
        $rag = new FakeRagStreamClient("event: done\ndata: {\"model\":\"m\"}\n\n");
        $request = Request::create('/ai-search/stream', 'POST', [
            'result_page_cursor' => 'signed.cursor',
            'page_context' => ['forged' => 'context'],
        ]);
        $request->setUserResolver(static fn (): User => new User);

        $response = $this->controller($rag)->stream($request);

        $this->assertSame(200, $response->getStatusCode());
        $this->assertNotNull($rag->lastStreamRequest);
        $this->assertNull($rag->lastStreamRequest->question);
        $this->assertSame('signed.cursor', $rag->lastStreamRequest->resultPageCursor);
        $this->assertNull($rag->lastStreamRequest->pageContext);
    }

    /** @test */
    public function it_rejects_a_result_page_cursor_for_regenerate_before_rag(): void
    {
        config(['services.rag.enabled' => true]);
        $rag = new FakeRagStreamClient;
        $request = Request::create('/ai-search/stream', 'POST', [
            'operation' => 'regenerate',
            'target_exchange_id' => str_repeat('a', 32),
            'result_page_cursor' => 'signed.cursor',
        ]);
        $request->setUserResolver(static fn (): User => new User);

        $response = $this->controller($rag)->stream($request);

        $this->assertSame(422, $response->getStatusCode());
        $this->assertSame('invalid_ask_request', $response->getData(true)['error']);
        $this->assertNull($rag->lastStreamRequest);
    }

    /** @test */
    public function it_rejects_a_blank_result_page_cursor_as_missing_input(): void
    {
        config(['services.rag.enabled' => true]);
        $request = Request::create('/ai-search/stream', 'POST', [
            'result_page_cursor' => '   ',
        ]);
        $request->setUserResolver(static fn (): User => new User);

        $response = $this->controller(new FakeRagStreamClient)->stream($request);

        $this->assertSame(422, $response->getStatusCode());
        $this->assertSame('invalid_ask_request', $response->getData(true)['error']);
    }

    /** @test */
    public function it_forwards_the_requested_detail_response_policy(): void
    {
        config(['services.rag.enabled' => true]);
        $rag = new FakeRagStreamClient("event: done\ndata: {\"model\":\"m\"}\n\n");
        $request = Request::create('/ai-search/stream', 'POST', [
            'question' => 'show every visible detail for this job',
            'response_policy' => 'strict',
        ]);
        $request->setUserResolver(static fn (): User => new User);

        $response = $this->controller($rag)->stream($request);

        $this->assertSame(200, $response->getStatusCode());
        $this->assertNotNull($rag->lastStreamRequest);
        $this->assertSame('strict', $rag->lastStreamRequest->responsePolicy);
    }

    /** @test */
    public function it_forwards_an_optional_idempotency_key_without_changing_request_xor(): void
    {
        config(['services.rag.enabled' => true]);
        $rag = new FakeRagStreamClient("event: done\ndata: {\"model\":\"m\"}\n\n");
        $request = Request::create('/ai-search/stream', 'POST', [
            'question' => 'show jobs',
            'idempotency_key' => 'public-key:turn-1',
        ]);
        $request->setUserResolver(static fn (): User => new User);

        $response = $this->controller($rag)->stream($request);

        $this->assertSame(200, $response->getStatusCode());
        $this->assertSame('public-key:turn-1', $rag->lastStreamRequest?->idempotencyKey);
        $this->assertSame('show jobs', $rag->lastStreamRequest?->question);
        $this->assertNull($rag->lastStreamRequest?->resultPageCursor);
    }

    /** @test */
    public function it_treats_a_blank_optional_idempotency_key_as_omitted(): void
    {
        config(['services.rag.enabled' => true]);
        $rag = new FakeRagStreamClient("event: done\ndata: {\"model\":\"m\"}\n\n");
        $request = Request::create('/ai-search/stream', 'POST', [
            'question' => 'show jobs',
            'idempotency_key' => '   ',
        ]);
        $request->setUserResolver(static fn (): User => new User);

        $response = $this->controller($rag)->stream($request);

        $this->assertSame(200, $response->getStatusCode());
        $this->assertNull($rag->lastStreamRequest?->idempotencyKey);
    }

    /** @test */
    public function it_rejects_an_oversized_idempotency_key_before_rag(): void
    {
        config(['services.rag.enabled' => true]);
        $rag = new FakeRagStreamClient;
        $request = Request::create('/ai-search/stream', 'POST', [
            'question' => 'show jobs',
            'idempotency_key' => str_repeat('k', 257),
        ]);
        $request->setUserResolver(static fn (): User => new User);

        $this->expectException(ValidationException::class);
        $this->controller($rag)->stream($request);
    }

    private function controller(FakeRagStreamClient $rag): AiSearchController
    {
        return new AiSearchController(
            ragClient: $rag,
            credits: new AiCreditsService,
            pageContextResolvers: new PageContextResolverRegistry([]),
        );
    }
}
