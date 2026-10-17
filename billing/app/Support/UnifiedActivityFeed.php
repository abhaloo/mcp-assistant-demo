<?php

declare(strict_types=1);

namespace App\Support;

use Carbon\Carbon;
use Illuminate\Http\Request;
use Illuminate\Pagination\LengthAwarePaginator;
use Illuminate\Support\Collection;
use Spatie\Activitylog\Models\Activity;

final class UnifiedActivityFeed
{
    /** Cap rows per query. Interim shim until full SQL union tracked in AUD plan §1.5. */
    private const ROW_CAP = 500;

    public static function paginate(Request $request, int $perPage = 10): LengthAwarePaginator
    {
        $rows = self::spatieRows($request)
            ->sortByDesc(fn (object $row) => $row->log_date)
            ->values();

        $page = max(1, (int) $request->input('page', 1));
        $total = $rows->count();
        $items = $rows->slice(($page - 1) * $perPage, $perPage)->values();

        return new LengthAwarePaginator(
            $items,
            $total,
            $perPage,
            $page,
            ['path' => $request->url(), 'query' => $request->query()]
        );
    }

    private static function spatieRows(Request $request): Collection
    {
        $query = Activity::with('causer')->orderByDesc('created_at');

        if ($request->filled('user_id')) {
            $query->where('causer_id', $request->input('user_id'));
        }
        if ($request->filled('log_type')) {
            $query->where('event', self::mapLogTypeToEvent((string) $request->input('log_type')));
        }
        if ($request->filled('table')) {
            $query->where('log_name', (string) $request->input('table'));
        }
        if ($request->filled('from_date') && $request->filled('to_date')) {
            $query->whereBetween('created_at', [
                $request->input('from_date') . ' 00:00:00',
                $request->input('to_date') . ' 23:59:59',
            ]);
        }

        if (auth()->check() && auth()->user()->entity_id) {
            $entityId = auth()->user()->entity_id;
            $query->where(function ($scoped) use ($entityId) {
                $scoped->where('properties->entity_id', $entityId)
                    ->orWhere(function ($auth) {
                        $auth->where('log_name', 'auth')->where('event', 'lockout');
                    });
            });
        }

        return $query->limit(self::ROW_CAP)->get()->map(fn (Activity $activity) => self::mapActivity($activity));
    }

    private static function mapActivity(Activity $activity): object
    {
        $properties = $activity->properties?->toArray() ?? [];

        if ($activity->event === 'updated' && isset($properties['old']) && is_array($properties['old'])) {
            $payload = $properties['old'];
        } elseif (isset($properties['attributes']) && is_array($properties['attributes'])) {
            $payload = $properties['attributes'];
        } else {
            $payload = is_array($properties) ? $properties : [];
        }

        if ($activity->subject) {
            $payload = array_merge(['id' => $activity->subject_id], $payload);
        }

        $logDate = $activity->created_at?->format('Y-m-d H:i:s') ?? '';

        return (object) [
            'id' => 'spatie-' . $activity->id,
            'user_id' => $activity->causer_id,
            'log_type' => self::mapEventToLogType($activity->event),
            'table_name' => $activity->subject?->getTable() ?? $activity->log_name,
            'log_date' => $logDate,
            'data' => json_encode($payload),
            'user' => $activity->causer,
            'dateHumanize' => $activity->created_at ? Carbon::parse($activity->created_at)->diffForHumans() : '',
            'json_data' => $payload,
        ];
    }

    private static function mapEventToLogType(?string $event): string
    {
        return match ($event) {
            'updated' => 'edit',
            'created' => 'create',
            'deleted' => 'delete',
            default => $event ?? 'unknown',
        };
    }

    private static function mapLogTypeToEvent(string $logType): string
    {
        return match ($logType) {
            'edit' => 'updated',
            'create' => 'created',
            'delete' => 'deleted',
            default => $logType,
        };
    }
}
