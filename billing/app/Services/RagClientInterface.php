<?php

declare(strict_types=1);

namespace App\Services;

use App\Models\User;
use App\Services\AskAi\ResolvedAskRequest;
use App\Services\AskAi\Restore\RestoreEvidenceInput;
use Psr\Http\Message\ResponseInterface;

interface RagClientInterface
{
    /**
     * @return array<string, mixed>
     */
    public function restoreEvidence(RestoreEvidenceInput $input, User $user): array;

    public function stream(ResolvedAskRequest $request): ResponseInterface;

    /**
     * @param  array<string, mixed>  $payload
     */
    public function streamV2(array $payload, User $user): ResponseInterface;

    /**
     * Buffered (non-streaming) JSON ask.
     *
     * @return array<string, mixed>
     */
    public function ask(ResolvedAskRequest $request): array;

    public function cancel(ResolvedAskRequest $request): void;

    /**
     * @param  array<string, mixed>  $payload
     * @return array<string, mixed>
     */
    public function feedback(array $payload, User $user): array;

    /**
     * @param  array<string, mixed>  $payload
     * @return array<string, mixed>
     */
    public function timing(array $payload, User $user): array;

    public function healthy(): bool;

    public function isAvailable(): bool;

    public function markUnavailable(): void;
}
