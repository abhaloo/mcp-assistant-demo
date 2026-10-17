<?php

declare(strict_types=1);

namespace App\Listeners;

use Illuminate\Queue\Events\JobProcessing;

final class ResetAuditRequestId
{
    public function handle(JobProcessing $event): void
    {
        if (app()->bound('audit.request_id')) {
            app()->forgetInstance('audit.request_id');
        }
    }
}
