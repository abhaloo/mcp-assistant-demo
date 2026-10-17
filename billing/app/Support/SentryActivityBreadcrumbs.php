<?php

namespace App\Support;

use App\Models\Concerns\LogsModelActivity;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Support\Facades\Event;
use Sentry\Breadcrumb;

/**
 * Mirrors model audit events into Sentry breadcrumbs for error context.
 */
final class SentryActivityBreadcrumbs
{
    private const EVENTS = [
        'eloquent.created: *' => ['verb' => 'created', 'config' => 'on_create'],
        'eloquent.updated: *' => ['verb' => 'edited', 'config' => 'on_edit'],
        'eloquent.deleted: *' => ['verb' => 'deleted', 'config' => 'on_delete'],
    ];

    public static function register(): void
    {
        if (! app()->bound('sentry')) {
            return;
        }

        foreach (self::EVENTS as $event => $meta) {
            Event::listen($event, static function (string $eventName, array $payload) use ($meta): void {
                if (! config('user-activity.activated', true)
                    || ! config('user-activity.log_events.' . $meta['config'], false)) {
                    return;
                }

                if (! auth()->check()) {
                    return;
                }

                $model = $payload[0] ?? null;

                if (! $model instanceof Model
                    || ! in_array(LogsModelActivity::class, class_uses_recursive($model), true)
                    || ($model->excludeLogging ?? false)) {
                    return;
                }

                \Sentry\addBreadcrumb(new Breadcrumb(
                    Breadcrumb::LEVEL_INFO,
                    Breadcrumb::TYPE_USER,
                    'user.activity',
                    sprintf('%s %s#%s', $meta['verb'], class_basename($model), (string) $model->getKey()),
                    ['table' => $model->getTable()]
                ));
            });
        }
    }
}
