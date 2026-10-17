<?php

declare(strict_types=1);

namespace Tests\Support;

use Illuminate\Testing\TestResponse;

/**
 * Capture streamed SSE bytes while driving the response closure to completion.
 * Restores PHPUnit's output-buffer stack depth afterwards.
 */
final class CapturedStreamOutput
{
    public static function from(TestResponse $response): string
    {
        $target = ob_get_level();
        ob_start();
        $response->baseResponse->sendContent();
        $output = (string) ob_get_clean();

        while (ob_get_level() < $target) {
            ob_start();
        }
        while (ob_get_level() > $target) {
            ob_end_clean();
        }

        return $output;
    }
}
