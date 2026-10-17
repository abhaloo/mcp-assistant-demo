<?php

declare(strict_types=1);

namespace Tests\Feature\Ai\Concerns;

use Illuminate\Support\Facades\View;

trait RendersAppLayout
{
    private function renderAppLayout(): string
    {
        // Laravel's @vite() reads a build manifest that does not exist in
        // this test environment for the unrelated resources/js/app.js entry
        // point the layout already, unconditionally, loads. Point it at the
        // Vite dev server ("hot" mode) so that unrelated call resolves —
        // this has no bearing on the v2-flag asset gating under test here.
        $hotFile = public_path('hot');
        file_put_contents($hotFile, 'http://127.0.0.1:5173');

        try {
            return View::make('layouts.app', ['sidebarActiveIds' => []])->render();
        } finally {
            if (file_exists($hotFile)) {
                unlink($hotFile);
            }
        }
    }

    private function panelAttribute(string $html, string $attribute): string
    {
        if (preg_match('/id="mcp-ask-ai-root"[^>]*'.preg_quote($attribute, '/').'=(["\'])(.*?)\1/s', $html, $matches)) {
            return html_entity_decode($matches[2], ENT_QUOTES | ENT_HTML5, 'UTF-8');
        }

        throw new \RuntimeException("Attribute [{$attribute}] not found on #mcp-ask-ai-root in rendered HTML.");
    }
}
