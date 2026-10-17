<?php

namespace App\Providers;

use Illuminate\Routing\Events\RouteMatched;
use Illuminate\Support\ServiceProvider;
use Livewire\Features\SupportEvents\SupportEvents;
use Livewire\WithFileUploads;
use function Livewire\on;

class DemoReadOnlyServiceProvider extends ServiceProvider
{
    /**
     * Register any application services.
     */
    public function register(): void
    {
        $this->mergeConfigFrom(
            config_path('demo_readonly.php'),
            'demo_readonly'
        );
    }

    /**
     * Bootstrap any application services.
     */
    public function boot(): void
    {
        if (! config('demo.readonly', true)) {
            return;
        }

        // 1. Global deny-by-default guard for Livewire method calls.
        // Hook API reference:
        // vendor/livewire/livewire/src/Mechanisms/HandleComponents/HandleComponents.php:494
        // trigger('call', $root, $method, $params, $context, $returnEarly)
        on('call', function ($component, $method, $params, $context, $returnEarly) {
            if (! config('demo.readonly', true)) {
                return;
            }

            if ($method === '_startUpload') {
                abort(403, 'File uploads are disabled in demo mode.');
            }

            $componentClass = get_class($component);
            $allowed = config("demo_readonly.{$componentClass}", []);

            if ($method === '__dispatch') {
                $eventName = $params[0] ?? null;
                $targetMethod = SupportEvents::getListenerMethodName($component, $eventName);
                if (! $targetMethod || ! in_array($targetMethod, $allowed, true)) {
                    abort(403, "Action [__dispatch:{$targetMethod}] on [{$componentClass}] is forbidden in read-only demo mode.");
                }

                return;
            }

            if (! in_array($method, $allowed, true)) {
                abort(403, "Action [{$method}] on [{$componentClass}] is forbidden in read-only demo mode.");
            }
        });

        // 2. Deny Livewire property updates that write or upload.
        // Hook API reference:
        // vendor/livewire/livewire/src/Mechanisms/HandleComponents/HandleComponents.php:355
        // trigger('update', $component, $path, $value)
        on('update', function ($component, $path, $value) {
            if (! config('demo.readonly', true)) {
                return;
            }

            if ($path === 'file' || $path === 'files' || str_starts_with($path, 'file.') || str_starts_with($path, 'files.')) {
                if (in_array(WithFileUploads::class, class_uses_recursive($component), true)) {
                    abort(403, 'File uploads are disabled in demo mode.');
                }
            }
        });

        // 3. Deny Livewire file upload HTTP endpoints.
        // Route reference:
        // vendor/livewire/livewire/src/Features/SupportFileUploads/SupportFileUploads.php:33
        // Route::post('/livewire/upload-file', [FileUploadController::class, 'handle'])
        $this->app['router']->matched(function (RouteMatched $event) {
            if (! config('demo.readonly', true)) {
                return;
            }

            if ($event->route->named('livewire.upload-file') || $event->request->is('livewire/upload-file*')) {
                abort(403, 'File uploads are disabled in demo mode.');
            }
        });
    }
}
