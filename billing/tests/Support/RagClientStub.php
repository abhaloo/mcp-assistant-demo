<?php

declare(strict_types=1);

namespace Tests\Support;

use App\Models\User;
use App\Services\AskAi\ResolvedAskRequest;
use App\Services\AskAi\Restore\RestoreEvidenceInput;
use App\Services\RagClientInterface;
use Psr\Http\Message\ResponseInterface;
use RuntimeException;

class RagClientStub implements RagClientInterface
{
    public function stream(ResolvedAskRequest $request): ResponseInterface
    {
        throw new RuntimeException('stream() not implemented in stub.');
    }

    public function streamV2(array $payload, User $user): ResponseInterface
    {
        throw new RuntimeException('streamV2() not implemented in stub.');
    }

    public function ask(ResolvedAskRequest $request): array
    {
        throw new RuntimeException('ask() not implemented in stub.');
    }

    public function cancel(ResolvedAskRequest $request): void
    {
        throw new RuntimeException('cancel() not implemented in stub.');
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
                    'run_id' => 'stub-run-1',
                    'exchange_id' => 'stub-exchange-1',
                    'answer_text' => 'Stub restored answer',
                    'sources' => [],
                    'citations' => ['parsed' => false, 'cited' => []],
                    'business_query' => null,
                    'presentation' => null,
                    'turn_result' => [
                        'version' => 1,
                        'outcome_type' => 'answered',
                        'completeness' => 'full',
                        'trusted' => false,
                        'selected' => [],
                        'omissions' => [],
                        'components' => [],
                    ],
                ],
            ], $input->references),
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

    public function markUnavailable(): void {}
}
