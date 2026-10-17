<?php

namespace App\Support;

use Illuminate\Support\Facades\Cache;

class LookupCache
{
    public const TTL = 300;

    public static function remember(string $key, callable $callback, int $ttl = self::TTL): mixed
    {
        $entityId = auth()->user()->entity_id ?? 0;

        return Cache::remember("lookup.{$entityId}.{$key}", $ttl, $callback);
    }

    public static function forget(string $key): void
    {
        $entityId = auth()->user()->entity_id ?? 0;
        Cache::forget("lookup.{$entityId}.{$key}");
    }
}
