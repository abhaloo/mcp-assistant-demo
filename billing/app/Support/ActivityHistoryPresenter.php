<?php

declare(strict_types=1);

namespace App\Support;

use App\Models\User;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Support\Collection;
use Spatie\Activitylog\Models\Activity;

final class ActivityHistoryPresenter
{
    /**
     * Spatie activities in the edit-history UI shape.
     *
     * @return Collection<int, array{date: string, user: string, data: array<string, mixed>}>
     */
    public static function forSubject(Model $subject): Collection
    {
        return Activity::forSubject($subject)
            ->whereIn('event', ['updated', 'created'])
            ->orderByDesc('created_at')
            ->get()
            ->map(fn (Activity $activity) => self::fromActivity($activity))
            ->sortByDesc('date')
            ->values();
    }

    /**
     * @return array{date: string, user: string, data: array<string, mixed>}
     */
    private static function fromActivity(Activity $activity): array
    {
        $properties = $activity->properties?->toArray() ?? [];

        if ($activity->event === 'updated' && isset($properties['old']) && is_array($properties['old'])) {
            $changes = $properties['old'];
        } elseif (isset($properties['attributes']) && is_array($properties['attributes'])) {
            $changes = $properties['attributes'];
        } else {
            $changes = $properties['old'] ?? [];
        }

        return [
            'date' => $activity->created_at?->toDateTimeString() ?? '',
            'user' => $activity->causer?->name ?? self::resolveUserName($activity->causer_id),
            'data' => is_array($changes) ? $changes : [],
        ];
    }

    private static function resolveUserName(mixed $userId): string
    {
        if (! $userId) {
            return 'Unknown User';
        }

        return User::find($userId)?->name ?? 'Unknown User';
    }
}
