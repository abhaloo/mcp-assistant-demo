<?php

declare(strict_types=1);

namespace App\Services;

use App\Authorization\AiAccessPolicy;
use App\Authorization\BusinessQuery\BusinessDefinitionsExporter;
use App\Authorization\PolicyManifestExporter;
use App\Models\User;
use App\Services\AskAi\RecordContext\RecordContextDigest;
use App\Services\AskAi\ResolvedAskRequest;
use App\Services\AskAi\Restore\RestoreEvidenceInput;
use App\Support\AskAiClock;
use Firebase\JWT\JWT;
use GuzzleHttp\Psr7\Response as Psr7Response;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Str;
use Psr\Http\Message\ResponseInterface;
use RuntimeException;

class RagClient implements RagClientInterface
{
    private const HEALTH_CACHE_SECONDS = 30;

    private const UNAVAILABLE_COOLDOWN_SECONDS = 120;

    private const TOKEN_TTL_SECONDS = 30;

    /**
     * Process-lifetime cache for the manifest hash (self-review finding,
     * Task B3): the manifest is entirely code-derived (no DB, no config, no
     * per-request input — see PolicyManifestExporter), so it cannot change
     * within a process's lifetime. Recomputing the JSON-encode + SHA-256 over
     * the full manifest on every single ask/stream/cancel/feedback request
     * was pure waste. Safe under persistent workers (Octane) precisely
     * BECAUSE this value is not user- or request-specific — unlike the
     * ambient-state bug Task B2 fixed in Bill::boot(), there is no "who is
     * asking" for this cache to leak between requests.
     */
    private static ?string $manifestHashCache = null;

    private static ?string $definitionBundleHashCache = null;

    private function manifestHash(): string
    {
        return self::$manifestHashCache ??= (new PolicyManifestExporter)->hash();
    }

    private function definitionBundleHash(): ?string
    {
        if (! config('services.rag.snapshot_v3', false)) {
            return null;
        }

        return self::$definitionBundleHashCache ??= (new BusinessDefinitionsExporter)->hash();
    }

    private function httpTimeoutSeconds(): int
    {
        $timeout = AskAiClock::scaleSeconds((int) config('services.rag.http_timeout', 90));

        return $timeout > 0 ? $timeout : 90;
    }

    private function buildAskPayload(ResolvedAskRequest $request): array
    {
        $payload = [];

        if ($request->question !== null) {
            $payload['question'] = $request->question;
        }

        if ($request->resultPageCursor !== null) {
            // A result-page cursor is an opaque capability minted by RAG. Forward it
            // as-is; never turn it into a visible question or ask the planner
            // to reconstruct the original request.
            $payload['result_page_cursor'] = $request->resultPageCursor;
        }

        // Preserve the legacy question-request byte shape while allowing a
        // cursor to occupy the same transport-neutral input slot.
        $payload['operation'] = $request->operation;

        if ($request->idempotencyKey !== null) {
            // Public but opaque: RAG owns the idempotency semantics and derives
            // the Answer Query ID from this exact value. Omit it entirely when
            // callers do not opt in so the legacy request bytes stay unchanged.
            $payload['idempotency_key'] = $request->idempotencyKey;
        }

        if ($request->threadId !== null) {
            $payload['thread_id'] = $request->threadId;
        }

        if ($request->targetExchangeId !== null) {
            $payload['target_exchange_id'] = $request->targetExchangeId;
        }

        if ($request->runId !== null) {
            $payload['run_id'] = $request->runId;
        }

        if ($request->pageContext !== null) {
            $payload['page_context'] = $request->pageContext->toWireArray();
        }

        // Task B4 / R3: additive — both the body field (here) and the JWT digest
        // claim (mintTokenForRequest()) attach together, matching RAG's four-way
        // enforcement (present/present, missing/missing; never one without the
        // other).
        if ($request->recordContext !== null) {
            $payload['record_context'] = $request->recordContext->toWireArray();
        }

        if ($request->responsePolicy !== 'allow_partial') {
            $payload['response_policy'] = $request->responsePolicy;
        }

        return $payload;
    }

    /**
     * Task B4 / R3: when the resolved request carries a trusted record context,
     * mint the `record_context_digest` claim from the SAME wire array the request
     * body carries (buildAskPayload() above) — one computation, matching values by
     * construction, never two independently-built representations that could
     * drift apart.
     */
    private function mintTokenForRequest(ResolvedAskRequest $request): string
    {
        $digest = $request->recordContext !== null
            ? RecordContextDigest::compute($request->recordContext->toWireArray())
            : null;

        return $this->mintToken($request->user, 'ask', $digest);
    }

    public function stream(ResolvedAskRequest $request): ResponseInterface
    {
        $url = rtrim((string) config('services.rag.url'), '/').'/api/ask';
        $token = $this->mintTokenForRequest($request);
        $body = json_encode($this->buildAskPayload($request), JSON_THROW_ON_ERROR);

        $stream = new CurlSseStream(
            url: $url,
            headers: [
                'Authorization: Bearer '.$token,
                'Accept: text/event-stream',
                'Content-Type: application/json',
                'traceparent: '.$this->traceparent(),
            ],
            body: $body,
            connectTimeout: 5,
            timeout: $this->httpTimeoutSeconds(),
        );

        $status = $stream->open();
        if ($status === 0) {
            $error = $stream->errorMessage();
            $stream->close();

            throw new RuntimeException('rag stream failed: '.($error !== '' ? $error : 'no response'));
        }

        return new Psr7Response($status, ['Content-Type' => 'text/event-stream'], $stream);
    }

    /**
     * @param  array<string, mixed>  $payload
     */
    public function streamV2(array $payload, User $user): ResponseInterface
    {
        $url = rtrim((string) config('services.rag.url'), '/').'/api/ask/v2';
        $digest = null;
        if (isset($payload['record_context']) && is_array($payload['record_context'])) {
            $digest = RecordContextDigest::compute($payload['record_context']);
        }
        $token = $this->mintToken($user, 'ask', $digest);
        $body = json_encode($payload, JSON_THROW_ON_ERROR);
        $readDeadlineAtMs = isset($payload['deadline_at_ms']) ? (int) $payload['deadline_at_ms'] : null;

        $stream = new CurlSseStream(
            url: $url,
            headers: [
                'Authorization: Bearer '.$token,
                'Accept: text/event-stream',
                'Content-Type: application/json',
                'traceparent: '.$this->traceparent(),
            ],
            body: $body,
            connectTimeout: 5,
            timeout: $this->httpTimeoutSeconds(),
            readDeadlineAtMs: $readDeadlineAtMs,
        );

        $status = $stream->open();
        if ($status === 0) {
            $error = $stream->errorMessage();
            $stream->close();

            throw new RuntimeException('rag stream failed: '.($error !== '' ? $error : 'no response'));
        }

        return new Psr7Response($status, ['Content-Type' => 'text/event-stream'], $stream);
    }

    public function ask(ResolvedAskRequest $request): array
    {
        $url = rtrim((string) config('services.rag.url'), '/').'/api/ask';
        $token = $this->mintTokenForRequest($request);

        $response = Http::withHeaders([
            'Authorization' => 'Bearer '.$token,
            'Accept' => 'application/json',
            'traceparent' => $this->traceparent(),
        ])
            ->timeout($this->httpTimeoutSeconds())
            ->connectTimeout(5)
            ->withOptions([
                'allow_redirects' => false,
            ])
            ->post($url, $this->buildAskPayload($request));

        return $response->throw()->json();
    }

    public function cancel(ResolvedAskRequest $request): void
    {
        if ($request->runId === null || $request->runId === '') {
            throw new RuntimeException('run_id is required for cancel.');
        }

        $url = rtrim((string) config('services.rag.url'), '/').'/api/cancel';
        $token = $this->mintToken($request->user, 'cancel');

        Http::withHeaders([
            'Authorization' => 'Bearer '.$token,
            'Accept' => 'application/json',
            'traceparent' => $this->traceparent(),
        ])
            ->timeout(10)
            ->connectTimeout(5)
            ->withOptions([
                'allow_redirects' => false,
            ])
            ->post($url, [
                'run_id' => $request->runId,
            ])
            ->throw();
    }

    public function feedback(array $payload, User $user): array
    {
        $url = rtrim((string) config('services.rag.url'), '/').'/api/feedback';
        $token = $this->mintToken($user, 'feedback');

        $response = Http::withHeaders([
            'Authorization' => 'Bearer '.$token,
            'Accept' => 'application/json',
            'traceparent' => $this->traceparent(),
        ])
            ->timeout(15)
            ->connectTimeout(5)
            ->withOptions([
                'allow_redirects' => false,
            ])
            ->post($url, $payload);

        return $response->throw()->json();
    }

    public function timing(array $payload, User $user): array
    {
        $url = rtrim((string) config('services.rag.url'), '/').'/api/query-record/timing';
        $token = $this->mintToken($user, 'ask');

        $response = Http::withHeaders([
            'Authorization' => 'Bearer '.$token,
            'Accept' => 'application/json',
            'traceparent' => $this->traceparent(),
        ])
            ->timeout(10)
            ->connectTimeout(5)
            ->withOptions([
                'allow_redirects' => false,
            ])
            ->post($url, $payload);

        return $response->throw()->json();
    }

    /**
     * @return array<string, mixed>
     */
    public function restoreEvidence(RestoreEvidenceInput $input, User $user): array
    {
        $url = rtrim((string) config('services.rag.url'), '/').'/api/restore-evidence';
        $token = $this->mintToken($user, 'restore_evidence');

        $response = Http::withHeaders([
            'Authorization' => 'Bearer '.$token,
            'Accept' => 'application/json',
            'traceparent' => $this->traceparent(),
        ])
            ->timeout(15)
            ->connectTimeout(5)
            ->withOptions([
                'allow_redirects' => false,
            ])
            ->post($url, [
                'thread_id' => $input->threadId,
                'references' => array_map(static fn (string $ref): array => ['restore_ref' => $ref], $input->references),
            ]);

        $data = $response->throw()->json();
        if (! is_array($data)) {
            throw new RuntimeException('restore-evidence response must be an array.');
        }

        $this->validateRestoreResponse($data);

        return $data;
    }

    /**
     * @param  array<string, mixed>  $data
     */
    private function validateRestoreResponse(array $data): void
    {
        if (($data['version'] ?? null) !== 1) {
            throw new RuntimeException('restore-evidence response version must be 1.');
        }

        if (! isset($data['results']) || ! is_array($data['results'])) {
            throw new RuntimeException('restore-evidence results must be an array.');
        }

        foreach ($data['results'] as $index => $item) {
            if (! is_array($item)) {
                throw new RuntimeException("restore-evidence result at index {$index} must be an array.");
            }

            $restoreRef = $item['restore_ref'] ?? null;
            if (! is_string($restoreRef) || $restoreRef === '') {
                throw new RuntimeException("restore-evidence result at index {$index} missing valid restore_ref.");
            }

            $status = $item['status'] ?? null;
            if (! in_array($status, ['authorized', 'denied', 'unavailable'], true)) {
                throw new RuntimeException("restore-evidence result at index {$index} has invalid status: ".json_encode($status));
            }

            $reason = $item['reason'] ?? null;
            if (! is_string($reason) || $reason === '') {
                throw new RuntimeException("restore-evidence result at index {$index} missing valid reason.");
            }

            $payload = $item['payload'] ?? null;
            if ($status === 'authorized') {
                if ($reason !== 'authorized') {
                    throw new RuntimeException("authorized restored evidence at index {$index} must have reason 'authorized'.");
                }
                if (! is_array($payload)) {
                    throw new RuntimeException("authorized restored evidence at index {$index} requires a payload array.");
                }
            } else {
                if ($payload !== null) {
                    throw new RuntimeException("restored evidence at index {$index} with status '{$status}' must not have a payload.");
                }
            }
        }
    }

    public function healthy(): bool
    {
        $cached = Cache::get('rag:health');
        if ($cached !== null) {
            return (bool) $cached;
        }

        $lock = Cache::lock('rag:health:probe', 5);
        if (! $lock->get()) {
            $deadline = microtime(true) + 3.0;
            while (microtime(true) < $deadline) {
                usleep(50_000);
                $cached = Cache::get('rag:health');
                if ($cached !== null) {
                    return (bool) $cached;
                }
            }

            return false;
        }

        try {
            return Cache::remember('rag:health', self::HEALTH_CACHE_SECONDS, function (): bool {
                try {
                    $url = rtrim((string) config('services.rag.url'), '/').'/healthz';

                    return Http::timeout(2)->connectTimeout(1)->get($url)->successful();
                } catch (\Throwable) {
                    return false;
                }
            });
        } finally {
            $lock->release();
        }
    }

    public function isAvailable(): bool
    {
        if (! config('services.rag.enabled', false)) {
            return false;
        }

        if ($this->isInCooldown()) {
            return false;
        }

        return $this->healthy();
    }

    public function markUnavailable(): void
    {
        Cache::put(
            'rag:unavailable_until',
            now()->addSeconds(self::UNAVAILABLE_COOLDOWN_SECONDS)->timestamp,
            self::UNAVAILABLE_COOLDOWN_SECONDS
        );
        Cache::forget('rag:health');
    }

    /**
     * Mint a short-lived one-time service JWT. Never returned to the browser.
     *
     * Task B3 / R3: when `services.rag.snapshot_v2` is enabled (default on this
     * branch), also attaches the v2 `record_access` claim — AiAccessPolicy::snapshot()
     * built from THIS SAME freshly loaded $user + the SAME explicit permission list
     * already used for the `permissions` claim, so the two can never disagree.
     * Disabled, mintToken() emits exactly the pre-B3 v1 claim set: the
     * `record_access` key is omitted entirely (not set to null) — byte-identical
     * to the token this method minted before this task.
     *
     * Task B4 / R3: $recordContextDigest, when non-null, attaches as the
     * `record_context_digest` claim — the caller (mintTokenForRequest()) is the
     * only place that ever passes a non-null value, so a bare `mintToken($user)`
     * call (cancel/feedback, or any test calling this directly) never gains the
     * claim — matching RAG's four-way enforcement (claim present <=> body field
     * present).
     */
    public function mintToken(User $user, string $scope = 'ask', ?string $recordContextDigest = null): string
    {
        $secret = config('services.rag.jwt_secret');
        if (! is_string($secret) || $secret === '') {
            throw new RuntimeException('RAG JWT secret is not configured.');
        }

        $departmentId = $user->department_id;
        if ($departmentId !== null) {
            $departmentId = (int) $departmentId;
        }

        $permissions = $user->getAllPermissions()->pluck('name')->all();

        $now = now()->timestamp;
        $payload = [
            'iss' => config('services.rag.jwt_iss', 'laravel-billing'),
            'aud' => config('services.rag.jwt_aud', 'rag-service'),
            'iat' => $now,
            'exp' => $now + self::TOKEN_TTL_SECONDS,
            'jti' => Str::uuid()->toString(),
            'scope' => $scope,
            'tool_result_version' => 1,
            'user_id' => $user->id,
            'role' => $user->primaryRoleName(),
            'permissions' => $permissions,
            'department_id' => $departmentId,
        ];

        if (config('services.rag.snapshot_v2', true)) {
            $policy = AiAccessPolicy::forUser($user, $permissions);
            $payload['record_access'] = $policy->snapshot(
                $this->manifestHash(),
                $this->definitionBundleHash(),
            );
        }

        if ($recordContextDigest !== null) {
            $payload['record_context_digest'] = $recordContextDigest;
        }

        return JWT::encode($payload, $secret, 'HS256');
    }

    private function isInCooldown(): bool
    {
        $until = Cache::get('rag:unavailable_until');

        return is_numeric($until) && (int) $until > now()->timestamp;
    }

    private function traceparent(): string
    {
        $traceId = bin2hex(random_bytes(16));
        $spanId = bin2hex(random_bytes(8));

        return "00-{$traceId}-{$spanId}-01";
    }
}
