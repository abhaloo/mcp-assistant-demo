<?php

namespace App\Listeners;

use App\Support\UserAgent;
use Illuminate\Auth\Events\Login;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Http;

class EnhancedLoginListener
{
    public function __construct(protected Request $request) {}

    public function handle(Login $event): void
    {
        if (! config('activitylog.auth_events.login', true)
            || ! config('activitylog.enabled', true)) {
            return;
        }

        $agent = UserAgent::fromRequest($this->request);

        $ip = $this->request->ip();
        $location = $this->getLocation($ip);

        $properties = [
            'ip' => $ip,
            'user_agent' => $this->request->userAgent(),
            'device_type' => $agent->deviceType() ?: 'Desktop',
            'browser' => $agent->browser(),
            'os' => $agent->platform(),
            'country' => $location['country'] ?? null,
            'city' => $location['city'] ?? null,
        ];

        if (isset($event->user->entity_id)) {
            $properties['entity_id'] = $event->user->entity_id;
        }

        if (app()->bound('audit.request_id')) {
            $properties['request_id'] = app('audit.request_id');
        }

        activity('auth')
            ->causedBy($event->user)
            ->withProperties($properties)
            ->event('login')
            ->log('user login');
    }

    private function getLocation(string $ip): array
    {
        if (in_array($ip, ['127.0.0.1', '::1']) || str_starts_with($ip, '192.168.') || str_starts_with($ip, '10.')) {
            return ['country' => 'Local', 'city' => 'Local'];
        }

        try {
            $response = Http::timeout(3)->get("http://ip-api.com/json/{$ip}?fields=country,city");
            if ($response->successful()) {
                return $response->json();
            }
        } catch (\Exception $e) {
            // Location is optional
        }

        return [];
    }
}
