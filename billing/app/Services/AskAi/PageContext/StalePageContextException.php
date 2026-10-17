<?php

declare(strict_types=1);

namespace App\Services\AskAi\PageContext;

use RuntimeException;

/**
 * One or more client IDs are missing/unauthorized for the current user.
 * Response must not disclose which ID failed.
 */
final class StalePageContextException extends RuntimeException
{
    public function __construct()
    {
        parent::__construct('stale_page_context');
    }
}
