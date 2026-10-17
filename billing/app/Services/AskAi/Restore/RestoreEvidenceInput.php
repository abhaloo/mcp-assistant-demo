<?php

declare(strict_types=1);

namespace App\Services\AskAi\Restore;

readonly class RestoreEvidenceInput
{
    /**
     * @param  list<string>  $references
     */
    public function __construct(
        public string $threadId,
        public array $references,
    ) {}
}
