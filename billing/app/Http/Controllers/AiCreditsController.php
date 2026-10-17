<?php

declare(strict_types=1);

namespace App\Http\Controllers;

use App\Services\AiCreditsService;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;

/**
 * The signed-in person's own monthly Ask AI spend, for the usage meter under
 * the composer. Read after a turn's stream has closed, so the figure already
 * includes that turn's charge.
 */
class AiCreditsController extends Controller
{
    public function __construct(private readonly AiCreditsService $credits) {}

    public function show(Request $request): JsonResponse
    {
        return response()->json($this->credits->meterPayload($request->user()));
    }
}
