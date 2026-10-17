<?php

declare(strict_types=1);

namespace App\Support;

use Symfony\Component\HttpFoundation\Response;

final class OutputBuffers
{
    /**
     * Drop every PHP-level output buffer so echoes reach the client as they
     * happen. flush() drains only the SAPI buffer; the ini-level buffer
     * (output_buffering, on by default under FPM and the built-in server)
     * holds early SSE frames until it overflows, which turns a live stream
     * into one end-of-turn burst.
     *
     * Plain CLI hardwires output_buffering off, so any buffer present there
     * belongs to someone else (a test harness capturing the stream) and must
     * be left alone. Symfony's helper skips non-removable handlers instead
     * of spinning on them.
     */
    public static function dropAll(): void
    {
        if (PHP_SAPI === 'cli') {
            return;
        }
        Response::closeOutputBuffers(0, true);
    }
}
