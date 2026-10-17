<?php

declare(strict_types=1);

namespace App\Http\Middleware;

use Closure;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Log;
use Illuminate\Support\Str;
use Symfony\Component\HttpFoundation\Response;

class AttachRequestContext
{
    public const ATTRIBUTE_KEY = 'ref_id';

    public function handle(Request $request, Closure $next): Response
    {
        $refId = self::generateRefId();

        $request->attributes->set(self::ATTRIBUTE_KEY, $refId);

        Log::shareContext([
            'ref_id' => $refId,
        ]);

        return $next($request);
    }

    public static function generateRefId(): string
    {
        return strtoupper(substr((string) Str::ulid(), 0, 12));
    }

    public static function refId(?Request $request = null): ?string
    {
        $request = $request ?? request();

        if ($request === null) {
            return null;
        }

        $refId = $request->attributes->get(self::ATTRIBUTE_KEY);

        return is_string($refId) && $refId !== '' ? $refId : null;
    }
}
