<?php

declare(strict_types=1);

namespace App\Listeners;

use Illuminate\Auth\Events\Lockout;
use Illuminate\Http\Request;

final class AuthLockoutListener
{
    public function __construct(private Request $request) {}

    public function handle(Lockout $event): void
    {
        if (! config('activitylog.auth_events.lockout', true)
            || ! config('activitylog.enabled', true)) {
            return;
        }

        $properties = [
            'ip' => $this->request->ip(),
            'user_agent' => $this->request->userAgent(),
        ];

        if (app()->bound('audit.request_id')) {
            $properties['request_id'] = app('audit.request_id');
        }

        activity('auth')
            ->withProperties($properties)
            ->event('lockout')
            ->log('account lockout');
    }
}
