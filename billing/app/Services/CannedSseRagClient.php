<?php

declare(strict_types=1);

namespace App\Services;

use App\Models\User;
use App\Providers\AppServiceProvider;
use App\Services\AskAi\ResolvedAskRequest;
use App\Services\AskAi\Restore\RestoreEvidenceInput;
use GuzzleHttp\Psr7\Response as Psr7Response;
use GuzzleHttp\Psr7\Utils;
use Psr\Http\Message\ResponseInterface;

/**
 * Deterministic, offline RAG upstream for browser (Dusk) smoke tests.
 *
 * The real {@see RagClient} talks to a FastAPI SSE service over HTTP. Dusk drives
 * a *served* app (php artisan serve), so an in-process container fake from the test
 * harness never reaches it — the served app needs a binding it can resolve itself.
 *
 * This class is wired only when config('services.rag.fake_stream') is true (set via
 * RAG_FAKE_STREAM in the Dusk environment — see {@see AppServiceProvider::register()}).
 * It NEVER activates in production: the flag defaults to false.
 *
 * stream() returns a canned text/event-stream body matching the §7.2 wire contract
 * in docs/superpowers/specs/2026-06-12-global-search-rag-integration-design.md:
 * a `: ping` heartbeat, `event: token` deltas, one `event: sources`, then `event: done`.
 */
final class CannedSseRagClient implements RagClientInterface
{
    /** Reassembled answer the token deltas below spell out (kept in sync for assertions). */
    public const ANSWER = 'Invoice INV-1001 is fully paid.';

    /** Model name surfaced in the card footer on the `done` event. */
    public const MODEL = 'canned-dusk-model';

    public function stream(ResolvedAskRequest $request): ResponseInterface
    {
        return new Psr7Response(
            200,
            ['Content-Type' => 'text/event-stream'],
            Utils::streamFor($this->cannedBody())
        );
    }

    public function ask(ResolvedAskRequest $request): array
    {
        return [
            'question' => $request->question,
            'answer' => self::ANSWER,
            'sources' => [],
            'model' => self::MODEL,
            'query_type' => 'structured',
        ];
    }

    public function healthy(): bool
    {
        return true;
    }

    public function isAvailable(): bool
    {
        return true;
    }

    public function markUnavailable(): void
    {
        // No-op: the canned upstream is always available.
    }

    public function cancel(ResolvedAskRequest $request): void
    {
        // No-op for Dusk canned stream.
    }

    public function feedback(array $payload, User $user): array
    {
        return ['success' => true];
    }

    public function timing(array $payload, User $user): array
    {
        return ['stored' => true];
    }

    /**
     * @return array<string, mixed>
     */
    public function restoreEvidence(RestoreEvidenceInput $input, User $user): array
    {
        return [
            'version' => 1,
            'results' => array_map(static fn (string $ref): array => [
                'restore_ref' => $ref,
                'status' => 'authorized',
                'reason' => 'authorized',
                'payload' => [
                    'thread_id' => $input->threadId,
                    'run_id' => 'canned-run-1',
                    'exchange_id' => 'canned-exchange-1',
                    'answer_text' => self::ANSWER,
                    'sources' => [],
                    'citations' => ['parsed' => false, 'cited' => []],
                    'business_query' => null,
                    'presentation' => null,
                    'turn_result' => [
                        'version' => 1,
                        'outcome_type' => 'answered',
                        'completeness' => 'full',
                        'trusted' => false,
                        'selected' => ['document_search'],
                        'omissions' => [],
                        'components' => [
                            [
                                'tool' => 'document_search',
                                'invocation_id' => 'doc-1',
                                'status' => 'succeeded',
                                'answer_query_ids' => [],
                                'evidence_digest' => null,
                            ],
                        ],
                    ],
                ],
            ], $input->references),
        ];
    }

    /**
     * Build the SSE payload. Token deltas concatenate to {@see self::ANSWER}; the
     * sources event carries two cited documents with title/snippet/access_tier; the
     * done event reports model + usage so the spend-cap path and footer both exercise.
     */
    private function cannedBody(): string
    {
        $tokens = ['Invoice ', 'INV-1001 ', 'is ', 'fully ', 'paid.'];

        $sources = [
            [
                'id' => 'doc-1',
                'title' => 'Invoice INV-1001',
                'snippet' => 'Total 1,250.00 — settled in full on 2026-05-30.',
                'access_tier' => 'finance',
            ],
            [
                'id' => 'doc-2',
                'title' => 'Payment receipt PR-4402',
                'snippet' => 'Receipt covering invoice INV-1001.',
                'access_tier' => 'finance',
            ],
        ];

        $lines = [];
        $lines[] = ': ping';
        $lines[] = '';

        foreach ([
            ['inspecting_access', 'Checking which data I can access'],
            ['running_query', 'Searching the database…'],
        ] as [$phase, $label]) {
            $lines[] = 'event: status';
            $lines[] = 'data: '.json_encode(['phase' => $phase, 'label' => $label], JSON_THROW_ON_ERROR);
            $lines[] = '';
        }

        foreach ($tokens as $delta) {
            $lines[] = 'event: token';
            $lines[] = 'data: '.json_encode(['d' => $delta], JSON_THROW_ON_ERROR);
            $lines[] = '';
        }

        $lines[] = 'event: sources';
        $lines[] = 'data: '.json_encode($sources, JSON_THROW_ON_ERROR);
        $lines[] = '';

        $lines[] = 'event: done';
        $lines[] = 'data: '.json_encode([
            'model' => self::MODEL,
            'usage' => ['input_tokens' => 12, 'output_tokens' => 8],
        ], JSON_THROW_ON_ERROR);
        $lines[] = '';

        return implode("\n", $lines)."\n";
    }
}
