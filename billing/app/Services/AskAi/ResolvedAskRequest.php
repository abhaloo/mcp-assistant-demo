<?php

declare(strict_types=1);

namespace App\Services\AskAi;

use App\Models\User;
use App\Services\AskAi\PageContext\TrustedPageContext;
use App\Services\AskAi\RecordContext\TrustedRecordContext;
use InvalidArgumentException;

/**
 * Immutable ask payload shared by JWT mint, stream(), ask(), cancel(), and JSON fallback.
 */
final readonly class ResolvedAskRequest
{
    public function __construct(
        public ?string $question,
        public User $user,
        public ?string $threadId = null,
        public ?TrustedPageContext $pageContext = null,
        public string $operation = 'ask',
        public ?string $targetExchangeId = null,
        public ?string $runId = null,
        public ?string $contextMode = null,
        public ?TrustedRecordContext $recordContext = null,
        public ?string $resultPageCursor = null,
        public string $responsePolicy = 'allow_partial',
        public ?string $idempotencyKey = null,
    ) {
        if (! in_array($this->responsePolicy, ['allow_partial', 'strict'], true)) {
            throw new InvalidArgumentException('Invalid Business Query response policy.');
        }
        if ($this->resultPageCursor !== null && trim($this->resultPageCursor) === '') {
            throw new InvalidArgumentException('Result page cursor must not be blank.');
        }

        if ($this->idempotencyKey !== null) {
            if (trim($this->idempotencyKey) === '') {
                throw new InvalidArgumentException('Idempotency key must not be blank.');
            }
            $length = function_exists('mb_strlen')
                ? mb_strlen($this->idempotencyKey, 'UTF-8')
                : strlen($this->idempotencyKey);
            if ($length > 256) {
                throw new InvalidArgumentException('Idempotency key must not exceed 256 characters.');
            }
        }

        if ($this->resultPageCursor !== null && $this->operation !== 'ask') {
            throw new InvalidArgumentException('Result page cursor is only valid for ask.');
        }

        if (($this->question === null) === ($this->resultPageCursor === null)) {
            throw new InvalidArgumentException(
                'Exactly one of question or result page cursor must be provided.',
            );
        }
    }

    public function effectiveContextMode(): string
    {
        if ($this->contextMode !== null) {
            return $this->contextMode;
        }

        return $this->pageContext !== null ? 'jobs' : 'none';
    }
}
