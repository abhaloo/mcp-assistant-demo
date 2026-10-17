<?php

declare(strict_types=1);

namespace App\Http\Middleware;

use App\Support\InteractionLogger;
use Closure;
use Illuminate\Http\Request;
use Symfony\Component\HttpFoundation\Response;

final class AuditHttpRequest
{
    public function handle(Request $request, Closure $next): Response
    {
        return $next($request);
    }

    public function terminate(Request $request, Response $response): void
    {
        if (! config('interaction-audit.enabled', false)) {
            return;
        }

        if (! auth()->check()) {
            return;
        }

        if ($request->is('_debugbar/*') || $request->is('livewire/*')) {
            return;
        }

        if (! in_array($request->method(), ['GET', 'POST'], true)) {
            return;
        }

        $path = ltrim($request->path(), '/');

        if (! self::matchesAllowlistedPrefix($path)) {
            return;
        }

        InteractionLogger::log('http', $request->method() . ' ' . $path, [
            'status' => $response->getStatusCode(),
            'route' => $request->route()?->getName(),
        ]);
    }

    private static function matchesAllowlistedPrefix(string $path): bool
    {
        foreach (config('interaction-audit.route_prefixes', []) as $prefix) {
            if ($path === $prefix || str_starts_with($path, $prefix . '/')) {
                return true;
            }
        }

        return false;
    }
}
