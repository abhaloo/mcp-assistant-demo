<?php

namespace App\Providers;

use App\Http\Controllers\HealthController;
use Illuminate\Cache\RateLimiting\Limit;
use Illuminate\Foundation\Support\Providers\RouteServiceProvider as ServiceProvider;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\RateLimiter;
use Illuminate\Support\Facades\Route;

class RouteServiceProvider extends ServiceProvider
{
    /**
     * The path to the "home" route for your application.
     *
     * Typically, users are redirected here after authentication.
     *
     * @var string
     */
    public const HOME = '/';

    /**
     * Define your route model bindings, pattern filters, and other route configuration.
     *
     * @return void
     */
    public function boot()
    {
        $this->configureRateLimiting();

        Route::get('/up', HealthController::class)->name('health.up');

        $this->routes(function () {
            Route::middleware('api')
                ->prefix('api')
                ->group(base_path('routes/api.php'));

            Route::middleware('web')
                ->group(base_path('routes/web.php'));
        });
    }

    /**
     * Configure the rate limiters for the application.
     *
     * @return void
     */
    protected function configureRateLimiting()
    {
        RateLimiter::for('api', function (Request $request) {
            return Limit::perMinute(60)->by($request->user()?->id ?: $request->ip());
        });

        RateLimiter::for('ai-api', function (Request $request) {
            $user = $request->user();
            $key = $user?->id ?: $request->ip();
            $role = $user ? strtolower($user->primaryRoleName()) : 'default';
            $limits = config('services.rag.limits', []);
            $perMinute = array_key_exists($role, $limits) ? $limits[$role] : ($limits['default'] ?? 20);

            if ($perMinute === null) {
                return Limit::none();
            }

            return Limit::perMinute((int) $perMinute)->by($key);
        });
    }
}
