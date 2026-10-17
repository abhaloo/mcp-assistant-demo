<?php

declare(strict_types=1);

namespace App\Models\Concerns;

use Spatie\Activitylog\Contracts\Activity;
use Spatie\Activitylog\LogOptions;
use Spatie\Activitylog\Traits\LogsActivity;

trait LogsModelActivity
{
    use LogsActivity;

    public function getActivitylogOptions(): LogOptions
    {
        return LogOptions::defaults()
            ->logAll()
            ->logOnlyDirty()
            ->dontSubmitEmptyLogs()
            ->useLogName($this->getTable())
            ->setDescriptionForEvent(fn (string $eventName) => class_basename($this) . ' ' . $eventName);
    }

    public function tapActivity(Activity $activity, string $eventName): void
    {
        $extra = [];

        if (isset($this->entity_id)) {
            $extra['entity_id'] = $this->entity_id;
        }

        if (app()->bound('audit.request_id')) {
            $extra['request_id'] = app('audit.request_id');
        }

        if ($extra !== []) {
            $activity->properties = $activity->properties->merge($extra);
        }
    }
}
