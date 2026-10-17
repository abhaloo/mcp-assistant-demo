<?php

declare(strict_types=1);

namespace App\Support;

use Illuminate\Support\Facades\DB;

final class InteractionLogger
{
    /**
     * @param  array<string, mixed>  $metadata
     */
    public static function log(string $source, string $action, array $metadata = []): void
    {
        if (! config('interaction-audit.enabled', false)) {
            return;
        }

        if (! auth()->check()) {
            return;
        }

        $user = auth()->user();

        DB::table('interaction_logs')->insert([
            'request_id' => app()->bound('audit.request_id') ? app('audit.request_id') : null,
            'user_id' => $user->id,
            'entity_id' => $user->entity_id ?? null,
            'source' => $source,
            'action' => $action,
            'metadata' => $metadata === [] ? null : json_encode($metadata),
            'ip' => request()?->ip(),
            'created_at' => now(),
        ]);
    }
}
