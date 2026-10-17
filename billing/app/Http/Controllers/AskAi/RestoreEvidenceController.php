<?php

declare(strict_types=1);

namespace App\Http\Controllers\AskAi;

use App\Http\Controllers\Controller;
use App\Http\Requests\RestoreEvidenceRequest;
use App\Models\User;
use App\Services\RagClientInterface;
use Illuminate\Http\JsonResponse;
use Illuminate\Support\Facades\Log;
use Throwable;

class RestoreEvidenceController extends Controller
{
    public function __construct(
        private readonly RagClientInterface $ragClient,
    ) {}

    public function __invoke(RestoreEvidenceRequest $request): JsonResponse
    {
        return $this->restore($request);
    }

    public function restore(RestoreEvidenceRequest $request): JsonResponse
    {
        $user = $request->user();
        if (! $user instanceof User) {
            return response()->json(['message' => 'Unauthenticated.'], 401);
        }

        try {
            $data = $this->ragClient->restoreEvidence($request->toInput(), $user);

            return response()->json($data, 200);
        } catch (Throwable $e) {
            $this->ragClient->markUnavailable();
            Log::channel('ai-search')->warning('ai-search restore-evidence upstream failed', [
                'user_id' => $user->id,
                'error' => $e->getMessage(),
            ]);

            return response()->json(['message' => 'AI search temporarily unavailable.'], 503);
        }
    }
}
