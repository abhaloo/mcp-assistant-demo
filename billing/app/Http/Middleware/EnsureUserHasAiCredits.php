<?php

declare(strict_types=1);

namespace App\Http\Middleware;

use App\Models\User;
use App\Services\AiCreditsService;
use Closure;
use Illuminate\Http\Request;
use Symfony\Component\HttpFoundation\Response;

class EnsureUserHasAiCredits
{
    public function __construct(private readonly AiCreditsService $credits) {}

    public function handle(Request $request, Closure $next): Response
    {
        $user = $request->user();

        if (! $user instanceof User) {
            return response()->json(['message' => 'Unauthenticated.'], 401);
        }

        if ($this->credits->isAtCap($user)) {
            return response()->json($this->credits->capPayload($user), 402);
        }

        return $next($request);
    }
}
