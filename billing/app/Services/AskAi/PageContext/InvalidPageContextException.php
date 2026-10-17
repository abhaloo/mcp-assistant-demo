<?php

declare(strict_types=1);

namespace App\Services\AskAi\PageContext;

use RuntimeException;

final class InvalidPageContextException extends RuntimeException
{
    public function __construct(
        string $message = 'Invalid page context.',
        public readonly string $category = 'malformed_structure',
        int $code = 0,
        ?\Throwable $previous = null,
    ) {
        parent::__construct($message, $code, $previous);
    }
}
