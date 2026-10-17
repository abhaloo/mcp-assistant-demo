<?php

declare(strict_types=1);

namespace App\Support;

use Illuminate\Database\Eloquent\Model;

final class BulkActivityLogger
{
    /**
     * Record a bulk query-builder operation that bypasses Eloquent model events.
     *
     * @param  array<string, mixed>  $context
     */
    public static function log(
        string $description,
        ?Model $subject = null,
        array $context = [],
        string $logName = 'bulk'
    ): void {
        if (! auth()->check()) {
            return;
        }

        $properties = $context;

        if (app()->bound('audit.request_id')) {
            $properties['request_id'] = app('audit.request_id');
        }

        if ($subject && isset($subject->entity_id)) {
            $properties['entity_id'] = $subject->entity_id;
        }

        $logger = activity($logName)
            ->causedBy(auth()->user())
            ->withProperties($properties)
            ->event('bulk');

        if ($subject) {
            $logger->performedOn($subject);
        }

        $logger->log($description);
    }
}
