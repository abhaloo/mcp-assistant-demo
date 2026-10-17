<?php

declare(strict_types=1);

namespace Tests\Support;

use App\Models\User;
use App\Services\AskAi\ResolvedAskRequest;
use Closure;
use GuzzleHttp\Psr7\Response as Psr7Response;
use GuzzleHttp\Psr7\Utils;
use Psr\Http\Message\ResponseInterface;

final class FakeRagStreamClient extends RagClientStub
{
    public ?ResolvedAskRequest $lastStreamRequest = null;

    public ?ResolvedAskRequest $lastAskRequest = null;

    public ?ResolvedAskRequest $lastCancelRequest = null;

    /** @var array<string, mixed>|null */
    public ?array $lastFeedbackPayload = null;

    public ?User $lastFeedbackUser = null;

    /** @var array<string, mixed>|null */
    public ?array $lastTimingPayload = null;

    public ?User $lastTimingUser = null;

    /**
     * @param  string|Closure(ResolvedAskRequest): string  $sseBody
     * @param  array<string, mixed>  $askAnswer  canned Answer returned by ask(); empty = simple stub
     */
    public function __construct(
        private readonly string|Closure $sseBody = '',
        private readonly array $askAnswer = [],
    ) {}

    /**
     * Track S PR fake: deterministic exchange/thread ids derived from run_id / user
     * (not random — random IDs can mask overlap/cross-talk the harness must catch).
     */
    public static function withRoleTaggedStreams(): self
    {
        return new self(
            sseBody: static fn (ResolvedAskRequest $request): string => self::roleTaggedBody($request),
            askAnswer: [
                'answer' => 'ok',
                'sources' => [],
                'model' => 'fake-concurrency',
                'query_type' => 'documentation',
            ],
        );
    }

    public function stream(ResolvedAskRequest $request): ResponseInterface
    {
        $this->lastStreamRequest = $request;

        $body = $this->sseBody instanceof Closure
            ? ($this->sseBody)($request)
            : $this->sseBody;

        return new Psr7Response(200, [
            'Content-Type' => 'text/event-stream',
        ], Utils::streamFor($body));
    }

    public ?array $lastStreamV2Payload = null;

    public ?User $lastStreamV2User = null;

    public function streamV2(array $payload, User $user): ResponseInterface
    {
        $this->lastStreamV2Payload = $payload;
        $this->lastStreamV2User = $user;

        $body = $this->sseBody instanceof Closure
            ? ($this->sseBody)(new ResolvedAskRequest(
                question: $payload['question'] ?? 'v2-question',
                user: $user,
                runId: $payload['run_id'] ?? null,
                threadId: $payload['thread_id'] ?? null,
            ))
            : $this->sseBody;

        return new Psr7Response(200, [
            'Content-Type' => 'text/event-stream',
        ], Utils::streamFor($body));
    }

    public function ask(ResolvedAskRequest $request): array
    {
        $this->lastAskRequest = $request;

        if ($this->askAnswer !== []) {
            return ['question' => $request->question, ...$this->askAnswer];
        }

        return [
            'question' => $request->question,
            'answer' => '',
            'sources' => [],
            'model' => 'fake',
            'query_type' => 'structured',
        ];
    }

    public function cancel(ResolvedAskRequest $request): void
    {
        $this->lastCancelRequest = $request;
    }

    public function feedback(array $payload, User $user): array
    {
        $this->lastFeedbackPayload = $payload;
        $this->lastFeedbackUser = $user;

        return ['success' => true];
    }

    public function timing(array $payload, User $user): array
    {
        $this->lastTimingPayload = $payload;
        $this->lastTimingUser = $user;

        return ['stored' => true];
    }

    private static function roleTaggedBody(ResolvedAskRequest $request): string
    {
        $runId = $request->runId ?? 'missing-run-id';
        // Deterministic: same run_id ⇒ same exchange_id (overlap becomes visible).
        $exchangeId = hash('sha256', 'ask-concurrency:exchange:'.$runId);
        // Prefer client thread; else stable per-user default (cross-principal leak visible).
        $threadId = $request->threadId
            ?? hash('sha256', 'ask-concurrency:thread:'.$request->user->id);

        return implode("\n", [
            ': ping',
            '',
            'event: status',
            'data: {"phase":"running_query","label":"Searching…"}',
            '',
            'event: token',
            'data: {"d":"ok"}',
            '',
            'event: done',
            'data: '.json_encode([
                'model' => 'fake-concurrency',
                'thread_id' => $threadId,
                'exchange_id' => $exchangeId,
                'run_id' => $runId,
                'usage' => ['input_tokens' => 1, 'output_tokens' => 1],
            ], JSON_THROW_ON_ERROR),
            '',
        ])."\n";
    }
}
