<?php

declare(strict_types=1);

namespace App\Http\Controllers;

use App\Http\Requests\AiSearchV2Request;
use App\Models\User;
use App\Rules\CorrelationId;
use App\Services\AiCreditsService;
use App\Services\AskAi\PageContext\ClientPageContext;
use App\Services\AskAi\PageContext\InvalidPageContextException;
use App\Services\AskAi\PageContext\PageContextResolverRegistry;
use App\Services\AskAi\PageContext\StalePageContextException;
use App\Services\AskAi\RecordContext\ClientRecordContext;
use App\Services\AskAi\RecordContext\InvalidRecordContextException;
use App\Services\AskAi\RecordContext\RecordContextResolver;
use App\Services\AskAi\RecordContext\RecordContextUnavailableException;
use App\Services\AskAi\ResolvedAskRequest;
use App\Services\RagClientInterface;
use App\Support\AskAiClock;
use App\Support\AskV2OutcomeReader;
use App\Support\OutputBuffers;
use App\Support\SseFrame;
use App\Support\SseUsageObserver;
use Illuminate\Http\Client\ConnectionException;
use Illuminate\Http\Client\RequestException;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Log;
use Psr\Http\Message\ResponseInterface;
use Sentry\Severity;
use Symfony\Component\HttpFoundation\StreamedResponse;
use Throwable;

class AiSearchController extends Controller
{
    public function __construct(
        private readonly RagClientInterface $ragClient,
        private readonly AiCreditsService $credits,
        private readonly PageContextResolverRegistry $pageContextResolvers,
        private readonly RecordContextResolver $recordContextResolver = new RecordContextResolver,
    ) {}

    public function stream(Request $request): StreamedResponse|JsonResponse
    {
        if (! config('services.rag.enabled', false)) {
            return response()->json(['message' => 'AI search is disabled.'], 503);
        }

        $validated = $request->validate([
            // A continuation intentionally omits question. `sometimes` keeps
            // Laravel from treating the absent sibling as a required input;
            // the explicit XOR check below owns the request-shape contract.
            'question' => ['sometimes', 'nullable', 'string', 'min:2', 'max:2000'],
            'result_page_cursor' => ['sometimes', 'nullable', 'string', 'max:8192'],
            'thread_id' => ['nullable', 'string', 'regex:/^[a-f0-9]{32}$/'],
            'operation' => ['nullable', 'string', 'in:ask,regenerate'],
            'target_exchange_id' => ['nullable', 'string', 'regex:/^[a-f0-9]{32}$/'],
            'run_id' => ['nullable', 'string', new CorrelationId],
            'page_context' => ['nullable', 'array'],
            'record_context' => ['nullable', 'array'],
            'response_policy' => ['nullable', 'string', 'in:allow_partial,strict'],
            // Optional and opaque. When absent, RagClient deliberately omits
            // the field to preserve the legacy question wire shape.
            'idempotency_key' => ['sometimes', 'nullable', 'string', 'max:256'],
        ]);

        $user = $request->user();
        if (! $user instanceof User) {
            return response()->json(['message' => 'Unauthenticated.'], 401);
        }

        $operation = $validated['operation'] ?? 'ask';
        if ($operation === 'regenerate' && empty($validated['target_exchange_id'])) {
            return response()->json([
                'error' => 'regenerate_conflict',
                'message' => 'Cannot regenerate this answer.',
            ], 409);
        }

        $question = isset($validated['question']) ? trim((string) $validated['question']) : null;
        $resultPageCursor = isset($validated['result_page_cursor'])
            ? trim((string) $validated['result_page_cursor'])
            : null;
        $hasQuestion = $question !== null && $question !== '';
        $hasResultPageCursor = $resultPageCursor !== null && $resultPageCursor !== '';
        if ($hasQuestion === $hasResultPageCursor) {
            return response()->json([
                'error' => 'invalid_ask_request',
                'message' => 'Provide exactly one question or result page cursor.',
                'errors' => [
                    'question' => ['Provide exactly one question or result page cursor.'],
                    'result_page_cursor' => ['Provide exactly one question or result page cursor.'],
                ],
            ], 422);
        }
        if (! $hasQuestion) {
            $question = null;
        }
        if (! $hasResultPageCursor) {
            $resultPageCursor = null;
        }
        $idempotencyKey = array_key_exists('idempotency_key', $validated)
            ? (string) $validated['idempotency_key']
            : null;
        if ($idempotencyKey !== null && trim($idempotencyKey) === '') {
            $idempotencyKey = null;
        }
        if ($resultPageCursor !== null && $operation !== 'ask') {
            return response()->json([
                'error' => 'invalid_ask_request',
                'message' => 'Result page cursor is only valid for ask.',
                'errors' => [
                    'result_page_cursor' => ['Result page cursor is only valid for ask.'],
                ],
            ], 422);
        }
        $threadId = isset($validated['thread_id']) ? trim((string) $validated['thread_id']) : null;
        if ($threadId === '') {
            $threadId = null;
        }

        try {
            $resolved = $this->buildResolvedRequest(
                question: $question,
                resultPageCursor: $resultPageCursor,
                user: $user,
                threadId: $threadId,
                pageContextPayload: $validated['page_context'] ?? null,
                operation: $operation,
                targetExchangeId: $validated['target_exchange_id'] ?? null,
                runId: $validated['run_id'] ?? $this->randomCorrelationId(),
                recordContextPayload: $validated['record_context'] ?? null,
                responsePolicy: $validated['response_policy'] ?? 'allow_partial',
                idempotencyKey: $idempotencyKey,
            );
        } catch (StalePageContextException) {
            return response()->json([
                'error' => 'stale_page_context',
                'message' => 'This page changed. Refresh and try again.',
            ], 409);
        } catch (InvalidPageContextException $e) {
            Log::channel('ai-search')->warning('ai-search invalid page context', [
                'user_id' => $user->id,
                'correlation_id' => $validated['run_id'] ?? null,
                'category' => $e->category,
            ]);

            return response()->json([
                'message' => 'Invalid page context.',
                'error' => 'invalid_page_context',
            ], 422);
        } catch (InvalidRecordContextException $e) {
            return response()->json([
                'message' => 'Invalid record context.',
                'error' => 'invalid_record_context',
            ], 422);
        } catch (RecordContextUnavailableException) {
            // Task 1B / Invariant 1, 6, 11: an explicit selection that cannot be
            // resolved terminates HERE with one generic result — never a
            // fallback to page_context, document RAG, SQL, or record tools.
            return response()->json([
                'error' => 'record_context_unavailable',
                'message' => 'The selected records are no longer available.',
            ], 409);
        }

        $startedAt = microtime(true);

        try {
            $upstream = $this->ragClient->stream($resolved);
            $status = $upstream->getStatusCode();

            if ($status < 200 || $status >= 300) {
                $this->ragClient->markUnavailable();

                return $this->mapUpstreamError($status, $upstream, $resolved->operation);
            }
        } catch (Throwable $e) {
            $this->ragClient->markUnavailable();
            Log::channel('ai-search')->warning('ai-search upstream failed', [
                'user_id' => $user->id,
                'correlation_id' => $resolved->runId,
                'operation' => $resolved->operation ?? 'ask',
                'error' => $e->getMessage(),
            ]);

            return response()->json(['message' => 'AI search temporarily unavailable.'], 503);
        }

        return response()->stream(function () use ($upstream, $resolved, $startedAt) {
            set_time_limit(0);
            // With buffers gone a client abort would kill the request at the
            // next echo, skipping outcome telemetry; the loop's own
            // connection_aborted() check owns disconnects instead.
            ignore_user_abort(true);
            OutputBuffers::dropAll();
            $body = $upstream->getBody();
            $deadline = time() + AskAiClock::scaleSeconds(90);
            $observer = new SseUsageObserver;
            $ttftMs = null;
            $fellBack = false;
            $timedOut = false;
            $user = $resolved->user;

            try {
                while (! $body->eof()) {
                    if (time() >= $deadline) {
                        $timedOut = true;
                        break;
                    }
                    $chunk = $body->read(8192);
                    if ($chunk === '') {
                        if ($body->eof()) {
                            break;
                        }

                        continue;
                    }

                    $observer->feed($chunk);

                    if (! $fellBack && $observer->outcome() === 'error:non_streamable_query_type') {
                        $fellBack = true;
                        $this->emitJsonFallback($resolved, $observer, $startedAt, $ttftMs);
                        break;
                    }

                    if ($ttftMs === null && ! SseUsageObserver::isHeartbeatOnly($chunk)) {
                        $ttftMs = (int) round((microtime(true) - $startedAt) * 1000);
                    }

                    echo $chunk;
                    flush();

                    if (connection_aborted()) {
                        $observer->markStopped();
                        break;
                    }
                }
            } catch (Throwable $e) {
                Log::channel('ai-search')->error('ai-search stream failed', [
                    'user_id' => $user->id,
                    'correlation_id' => $resolved->runId,
                    'operation' => $resolved->operation,
                    'error' => $e->getMessage(),
                ]);
                if (! str_starts_with((string) $observer->outcome(), 'error')) {
                    $this->emitStreamError(
                        $observer,
                        'internal_error',
                        'Something went wrong while streaming your answer.',
                    );
                }
            } finally {
                $body->close();
            }

            $outcome = $observer->outcome();

            if ($timedOut && $outcome !== 'done' && ! str_starts_with((string) $outcome, 'error')) {
                $this->emitStreamError(
                    $observer,
                    'stream_timeout',
                    'This is taking too long. Try again in a moment.',
                );
                $outcome = $observer->outcome();
            }

            if ($outcome === 'incomplete') {
                $this->emitStreamError(
                    $observer,
                    'incomplete_stream',
                    'The answer did not finish. Try again.',
                );
                $outcome = $observer->outcome();
            }

            if (str_starts_with((string) $outcome, 'error')) {
                \Sentry\captureMessage('AI search stream failed: '.$outcome, Severity::warning());
            }

            if ($outcome === 'done') {
                $this->credits->incrementFromUsage($user, $observer->usage(), $observer->model() ?? 'default', $observer->answerChars());
            }

            Log::channel('ai-search')->info('ai-search ask', [
                'user_id' => $user->id,
                'correlation_id' => $resolved->runId,
                'operation' => $resolved->operation,
                'context_mode' => $resolved->effectiveContextMode(),
                'ttft_ms' => $ttftMs,
                'total_ms' => (int) round((microtime(true) - $startedAt) * 1000),
                'outcome' => $outcome,
                'usage' => $observer->usage(),
                'cap_pct' => $this->credits->capPercent($user),
            ]);
        }, 200, [
            'Content-Type' => 'text/event-stream',
            'Cache-Control' => 'no-cache, no-transform',
            'X-Accel-Buffering' => 'no',
        ]);
    }

    public function cancel(Request $request): JsonResponse
    {
        if (! config('services.rag.enabled', false)) {
            return response()->json(['message' => 'AI search is disabled.'], 503);
        }

        $validated = $request->validate([
            'run_id' => ['required', 'string', new CorrelationId],
        ]);

        $user = $request->user();
        if (! $user instanceof User) {
            return response()->json(['message' => 'Unauthenticated.'], 401);
        }

        try {
            $this->ragClient->cancel(new ResolvedAskRequest(
                question: '',
                user: $user,
                runId: $validated['run_id'],
                operation: 'cancel',
            ));
        } catch (Throwable $e) {
            Log::channel('ai-search')->warning('ai-search cancel failed', [
                'user_id' => $user->id,
                'correlation_id' => $validated['run_id'],
                'error' => $e->getMessage(),
            ]);

            return response()->json(['message' => 'Cancel failed.'], 502);
        }

        Log::channel('ai-search')->info('ai-search cancel', [
            'user_id' => $user->id,
            'correlation_id' => $validated['run_id'],
        ]);

        return response()->json(['success' => true]);
    }

    public function feedback(Request $request): JsonResponse
    {
        $validated = $request->validate([
            'trace_id' => ['required', 'string', new CorrelationId],
            'feedback_token' => ['required', 'string', 'max:512'],
            'verdict' => ['required', 'string', 'in:up,down'],
            'reason' => ['nullable', 'string', 'in:incorrect,incomplete,irrelevant,unsafe,other'],
            'comment' => ['nullable', 'string', 'max:500'],
        ]);

        $user = $request->user();
        if (! $user instanceof User) {
            return response()->json(['message' => 'Unauthenticated.'], 401);
        }

        if (! config('services.rag.enabled', false)) {
            return response()->json(['message' => 'AI search is disabled.'], 503);
        }

        return $this->proxyRagLateWrite(
            logEvent: 'feedback',
            failMessage: 'Feedback failed.',
            correlationId: $validated['trace_id'],
            user: $user,
            call: fn () => $this->ragClient->feedback([
                'trace_id' => $validated['trace_id'],
                'feedback_token' => $validated['feedback_token'],
                'verdict' => $validated['verdict'],
                'reason' => $validated['reason'] ?? null,
                'comment' => isset($validated['comment']) ? mb_substr(trim($validated['comment']), 0, 500) : null,
            ], $user),
        );
    }

    public function timing(Request $request): JsonResponse
    {
        $validated = $request->validate([
            'trace_id' => ['required', 'string', new CorrelationId],
            'feedback_token' => ['required', 'string', 'max:512'],
            'ui_first_text_ms' => ['required', 'integer', 'min:0'],
            'completion_latency_ms' => ['required', 'integer', 'min:0'],
            // v2 UI timing receipt: Billing's own clock, kept distinct from
            // the server-side (RAG) timings recorded on the Query Record.
            'ui_first_activity_ms' => ['sometimes', 'nullable', 'integer', 'min:0'],
            'ui_first_row_ms' => ['sometimes', 'nullable', 'integer', 'min:0'],
            'ui_completion_latency_ms' => ['sometimes', 'nullable', 'integer', 'min:0'],
        ]);

        $user = $request->user();
        if (! $user instanceof User) {
            return response()->json(['message' => 'Unauthenticated.'], 401);
        }

        if (! config('services.rag.enabled', false)) {
            return response()->json(['message' => 'AI search is disabled.'], 503);
        }

        $payload = [
            'trace_id' => $validated['trace_id'],
            'feedback_token' => $validated['feedback_token'],
            'ui_first_text_ms' => $validated['ui_first_text_ms'],
            'completion_latency_ms' => $validated['completion_latency_ms'],
        ];

        if (array_key_exists('ui_first_activity_ms', $validated) && $validated['ui_first_activity_ms'] !== null) {
            $payload['ui_first_activity_ms'] = $validated['ui_first_activity_ms'];
            // ui_first_progress_ms is an ALIAS of first activity, not a
            // second event kind — derived here from the same measurement
            // rather than trusted as an independently client-submitted
            // value that could disagree with it.
            $payload['ui_first_progress_ms'] = $validated['ui_first_activity_ms'];
        }
        if (array_key_exists('ui_first_row_ms', $validated) && $validated['ui_first_row_ms'] !== null) {
            $payload['ui_first_row_ms'] = $validated['ui_first_row_ms'];
        }
        if (array_key_exists('ui_completion_latency_ms', $validated) && $validated['ui_completion_latency_ms'] !== null) {
            $payload['ui_completion_latency_ms'] = $validated['ui_completion_latency_ms'];
        }

        return $this->proxyRagLateWrite(
            logEvent: 'timing',
            failMessage: 'Timing failed.',
            correlationId: $validated['trace_id'],
            user: $user,
            call: fn () => $this->ragClient->timing($payload, $user),
            extraSuccessContext: $payload,
        );
    }

    /**
     * @param  array<string, mixed>  $extraSuccessContext
     */
    private function proxyRagLateWrite(
        string $logEvent,
        string $failMessage,
        string $correlationId,
        User $user,
        callable $call,
        array $extraSuccessContext = [],
    ): JsonResponse {
        try {
            $call();
        } catch (RequestException|ConnectionException $e) {
            Log::channel('ai-search')->warning("ai-search {$logEvent} proxy failed", [
                'user_id' => $user->id,
                'correlation_id' => $correlationId,
                'status' => $e instanceof RequestException ? $e->response?->status() : null,
            ]);

            return response()->json(['message' => $failMessage], 502);
        }

        Log::channel('ai-search')->info("ai-search {$logEvent}", array_merge([
            'user_id' => $user->id,
            'correlation_id' => $correlationId,
        ], $extraSuccessContext));

        return response()->json(['success' => true]);
    }

    /**
     * @param  array<string, mixed>|null  $pageContextPayload
     * @param  array<string, mixed>|null  $recordContextPayload
     *
     * @throws InvalidPageContextException
     * @throws StalePageContextException
     * @throws InvalidRecordContextException
     * @throws RecordContextUnavailableException
     */
    private function buildResolvedRequest(
        ?string $question,
        User $user,
        ?string $threadId,
        ?array $pageContextPayload,
        string $operation = 'ask',
        ?string $targetExchangeId = null,
        ?string $runId = null,
        ?array $recordContextPayload = null,
        ?string $resultPageCursor = null,
        string $responsePolicy = 'allow_partial',
        ?string $idempotencyKey = null,
    ): ResolvedAskRequest {
        if ($resultPageCursor !== null) {
            // A result-page cursor is already bound to the original authorized plan.
            // Do not re-resolve ambient or selected context and accidentally
            // widen or alter the server-owned cursor request.
            return new ResolvedAskRequest(
                question: $question,
                user: $user,
                threadId: $threadId,
                operation: $operation,
                targetExchangeId: $targetExchangeId,
                runId: $runId,
                resultPageCursor: $resultPageCursor,
                responsePolicy: $responsePolicy,
                idempotencyKey: $idempotencyKey,
            );
        }

        // Task 1B / Invariant 1, 2: an explicit record_context selection is
        // intentional and narrow; ambient page_context is incidental. The
        // presence of a valid, enabled client record_context IS the
        // precedence decision — browser precedence (mcp-ask-ai.js) is only a
        // usability optimization, this is the independent server-side
        // enforcement. page_context is neither resolved nor forwarded for
        // this request, even if the browser (forged or not) sent both.
        //
        // Task B4 / R3 rollback: when the flag is off, an incoming
        // record_context key is never even parsed — byte-identical to a
        // pre-B4 request (no body field, no JWT claim), regardless of what
        // the client sent, and page_context resolves as if record_context
        // were never sent at all.
        if ($recordContextPayload !== null && config('services.rag.record_context', false)) {
            $clientRecordContext = ClientRecordContext::fromArray($recordContextPayload);
            $trustedRecordContext = $this->recordContextResolver->resolve($clientRecordContext, $user);

            if ($trustedRecordContext === null) {
                throw new RecordContextUnavailableException;
            }

            return new ResolvedAskRequest(
                question: $question,
                user: $user,
                threadId: $threadId,
                operation: $operation,
                targetExchangeId: $targetExchangeId,
                runId: $runId,
                recordContext: $trustedRecordContext,
                resultPageCursor: $resultPageCursor,
                responsePolicy: $responsePolicy,
                idempotencyKey: $idempotencyKey,
            );
        }

        $trusted = null;
        if ($pageContextPayload !== null) {
            $client = ClientPageContext::fromArray($pageContextPayload);
            $trusted = $this->pageContextResolvers->resolve($client, $user);
        }

        return new ResolvedAskRequest(
            question: $question,
            user: $user,
            threadId: $threadId,
            pageContext: $trusted,
            operation: $operation,
            targetExchangeId: $targetExchangeId,
            runId: $runId,
            resultPageCursor: $resultPageCursor,
            responsePolicy: $responsePolicy,
            idempotencyKey: $idempotencyKey,
        );
    }

    /**
     * Fallback correlation ID when the caller sends none. Matches the same
     * 32-lowercase-hex shape App\Rules\CorrelationId enforces — the
     * previous Str::random(32) fallback was base62, which RAG's stricter
     * hex validation would reject downstream (F25).
     */
    private function randomCorrelationId(): string
    {
        return bin2hex(random_bytes(16));
    }

    private function emitJsonFallback(
        ResolvedAskRequest $resolved,
        SseUsageObserver $observer,
        float $startedAt,
        ?int &$ttftMs,
    ): void {
        try {
            $answer = $this->ragClient->ask($resolved);
        } catch (Throwable $e) {
            // This is the JSON-fallback path (emitJsonFallback's one caller fires
            // only when streaming itself failed non_streamable_query_type) — not
            // a fourth normal leg. Still carries the correlation ID so a fallback
            // failure is traceable like the other three legs.
            Log::channel('ai-search')->error('ai-search fallback failed', [
                'correlation_id' => $resolved->runId,
                'error' => $e->getMessage(),
            ]);

            $this->emitFrame($observer, SseFrame::event('error', [
                'error' => 'internal_error',
                'detail' => 'AI search failed.',
            ]));

            return;
        }

        foreach (SseFrame::framesForAnswer($answer) as $frame) {
            $isToken = str_starts_with($frame, 'event: token');

            if ($isToken && $ttftMs === null) {
                $ttftMs = (int) round((microtime(true) - $startedAt) * 1000);
            }

            $this->emitFrame($observer, $frame);

            if ($isToken) {
                usleep(50000);

                if (connection_aborted()) {
                    $observer->markStopped();

                    return;
                }
            }
        }
    }

    private function emitFrame(SseUsageObserver $observer, string $frame): void
    {
        $observer->feed($frame);

        echo $frame;
        flush();
    }

    private function emitStreamError(SseUsageObserver $observer, string $code, string $detail): void
    {
        $this->emitFrame($observer, SseFrame::event('error', [
            'error' => $code,
            'detail' => $detail,
        ]));
    }

    private function mapUpstreamError(int $status, ResponseInterface $upstream, string $operation): JsonResponse
    {
        if ($status === 409) {
            $body = (string) $upstream->getBody();
            $decoded = json_decode($body, true);
            $detail = is_array($decoded) ? ($decoded['detail'] ?? null) : null;

            if ($detail === 'regenerate_conflict' || ($decoded['error'] ?? null) === 'regenerate_conflict') {
                return response()->json([
                    'error' => 'regenerate_conflict',
                    'message' => 'Cannot regenerate this answer.',
                ], 409);
            }

            if ($detail === 'stale_page_context'
                || ($decoded['error'] ?? null) === 'stale_page_context') {
                return response()->json([
                    'error' => 'stale_page_context',
                    'message' => 'This page changed. Refresh and try again.',
                ], 409);
            }
        }

        if ($status === 503) {
            return response()->json(['message' => 'AI search temporarily unavailable.'], 503);
        }

        return response()->json(['message' => 'AI search failed.'], 502);
    }

    public function streamV2(AiSearchV2Request $request): StreamedResponse|JsonResponse
    {
        if (! config('services.rag.enabled', false)) {
            return response()->json(['message' => 'AI search is disabled.'], 503);
        }

        if (! config('ask_ai.ask_v2_enabled', false)) {
            return response()->json(['message' => 'Ask Assistant v2 is disabled.'], 404);
        }

        $user = $request->user();
        if (! $user instanceof User) {
            return response()->json(['message' => 'Unauthenticated.'], 401);
        }

        $validated = $request->validated();
        $runId = (string) $validated['run_id'];

        // The absolute ingress deadline is computed here, once, after Form
        // Request validation completes. A browser-supplied deadline_at_ms is
        // never honoured and never extends this budget — the value that
        // crosses to RAG is always this server-computed one, carried over
        // the same service-authenticated request RagClient already mints.
        $deadlineAtMs = (int) now()->valueOf() + AskAiClock::scaleMs((int) config('ask_ai.deadline_budget_ms', 25000));
        if (array_key_exists('deadline_at_ms', $validated) && $validated['deadline_at_ms'] !== null) {
            Log::channel('ai-search')->warning('ai-search v2 client-supplied deadline ignored', [
                'user_id' => $user->id,
                'correlation_id' => $runId,
                'operation' => $validated['operation'] ?? 'new_question',
            ]);
        }
        $validated['deadline_at_ms'] = $deadlineAtMs;

        try {
            $validated = $this->resolveV2TrustedContexts($validated, $user);
        } catch (StalePageContextException) {
            return response()->json([
                'error' => 'stale_page_context',
                'message' => 'This page changed. Refresh and try again.',
            ], 409);
        } catch (InvalidPageContextException $e) {
            Log::channel('ai-search')->warning('ai-search v2 invalid page context', [
                'user_id' => $user->id,
                'correlation_id' => $runId,
                'category' => $e->category,
            ]);

            return response()->json([
                'message' => 'Invalid page context.',
                'error' => 'invalid_page_context',
            ], 422);
        } catch (InvalidRecordContextException) {
            return response()->json([
                'message' => 'Invalid record context.',
                'error' => 'invalid_record_context',
            ], 422);
        } catch (RecordContextUnavailableException) {
            return response()->json([
                'error' => 'record_context_unavailable',
                'message' => 'The selected records are no longer available.',
            ], 409);
        }

        try {
            $upstream = $this->ragClient->streamV2($validated, $user);
            $status = $upstream->getStatusCode();

            if ($status < 200 || $status >= 300) {
                if ($status >= 500) {
                    $this->ragClient->markUnavailable();
                }

                return $this->mapUpstreamError($status, $upstream, $validated['operation'] ?? 'new_question');
            }
        } catch (Throwable $e) {
            $this->ragClient->markUnavailable();
            Log::channel('ai-search')->warning('ai-search v2 upstream failed', [
                'user_id' => $user->id,
                'correlation_id' => $runId,
                'operation' => $validated['operation'] ?? 'new_question',
                'error' => $e->getMessage(),
            ]);

            return response()->json(['message' => 'AI search temporarily unavailable.'], 503);
        }

        return response()->stream(function () use ($upstream, $deadlineAtMs, $runId, $user) {
            set_time_limit(0);
            // With buffers gone a client abort would kill the request at the
            // next echo; the loop's own connection_aborted() break and the
            // finally that closes the upstream own the disconnect instead.
            ignore_user_abort(true);
            OutputBuffers::dropAll();
            $body = $upstream->getBody();
            $terminalSeen = false;
            $terminalTail = '';
            $deadlineExceeded = false;

            try {
                while (! $body->eof()) {
                    if ((int) now()->valueOf() >= $deadlineAtMs) {
                        $deadlineExceeded = true;
                        break;
                    }

                    $chunk = $body->read(8192);
                    if ($chunk === '') {
                        if ($body->eof()) {
                            break;
                        }

                        if (connection_aborted()) {
                            break;
                        }

                        if ((int) now()->valueOf() >= $deadlineAtMs) {
                            $deadlineExceeded = true;
                            break;
                        }

                        continue;
                    }

                    // A chunk that only became available after the shared
                    // deadline passed is late truth: it is discarded, never
                    // forwarded to the browser as if it arrived on time.
                    if ((int) now()->valueOf() >= $deadlineAtMs) {
                        $deadlineExceeded = true;
                        break;
                    }

                    echo $chunk;
                    flush();

                    if ($terminalSeen) {
                        $terminalTail .= $chunk;
                    } else {
                        $markerAt = self::terminalMarkerOffset($chunk);
                        if ($markerAt !== null) {
                            $terminalSeen = true;
                            $terminalTail = substr($chunk, $markerAt);
                        }
                    }

                    // The terminal frame can span several reads. Stop only once its
                    // blank-line terminator has been forwarded, never at a chunk edge.
                    if ($terminalSeen && str_contains($terminalTail, "\n\n")) {
                        break;
                    }

                    if (connection_aborted()) {
                        break;
                    }
                }
            } finally {
                $body->close();
            }

            // The per-user cap is charged from RAG's priced figure on the
            // outcome frame, inside the relay's own finally ordering — the
            // bytes it meters have already reached the browser.
            if ($terminalSeen && (bool) config('services.rag.credits.charge_v2', false)) {
                $budget = AskV2OutcomeReader::budgetFromTerminalTail($terminalTail);

                if ($budget !== null && ($budget['this_turn_usd'] ?? null) !== null) {
                    $this->credits->incrementUsd($user, (float) $budget['this_turn_usd']);
                }
            }

            if ($deadlineExceeded && ! $terminalSeen) {
                echo SseFrame::event('stream_error', [
                    'code' => 'deadline_exceeded',
                    'message' => 'This is taking too long. Try again in a moment.',
                    'retryable' => true,
                    'run_id' => $runId,
                ]);
                flush();
            }
        }, 200, [
            'Content-Type' => 'text/event-stream',
            'Cache-Control' => 'no-cache, no-transform',
            'X-Accel-Buffering' => 'no',
        ]);
    }

    /**
     * @param  array<string, mixed>  $validated
     * @return array<string, mixed>
     */
    private function resolveV2TrustedContexts(array $validated, User $user): array
    {
        $recordPayload = $validated['record_context'] ?? null;

        if ($recordPayload !== null && config('services.rag.record_context', false)) {
            $clientRecordContext = ClientRecordContext::fromArray($recordPayload);
            $trustedRecordContext = $this->recordContextResolver->resolve($clientRecordContext, $user);

            if ($trustedRecordContext === null) {
                throw new RecordContextUnavailableException;
            }

            $validated['record_context'] = $trustedRecordContext->toWireArray();
            unset($validated['page_context']);

            return $validated;
        }

        unset($validated['record_context']);

        if (($validated['page_context'] ?? null) !== null) {
            $client = ClientPageContext::fromArray($validated['page_context']);
            $trusted = $this->pageContextResolvers->resolve($client, $user);

            if ($trusted === null) {
                unset($validated['page_context']);
            } else {
                $validated['page_context'] = $trusted->toWireArray();
            }
        }

        return $validated;
    }

    /**
     * Byte offset of the first terminal event marker in a relay chunk, or null.
     */
    private static function terminalMarkerOffset(string $chunk): ?int
    {
        $offset = null;
        foreach (['event: turn_outcome', 'event: stream_error'] as $marker) {
            $position = strpos($chunk, $marker);
            if ($position !== false && ($offset === null || $position < $offset)) {
                $offset = $position;
            }
        }

        return $offset;
    }
}
