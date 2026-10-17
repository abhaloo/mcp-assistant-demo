<?php

namespace App\Providers;

use App\Actions\Sidebar\BuildSidebarMenu;
use App\Http\Livewire\Dashboard\LatestInvoicesComponent;
use App\Http\Livewire\Dashboard\LatestPurchasesComponent;
use App\Models\Bill;
use App\Models\WorkOrder;
use App\Observers\WorkOrderObserver;
use App\Services\AiCreditsService;
use App\Services\AskAi\PageContext\JobsPageContextResolver;
use App\Services\AskAi\PageContext\PageContextResolverRegistry;
use App\Services\CannedSseRagClient;
use App\Services\RagClient;
use App\Services\RagClientInterface;
use App\Support\LivewireInteractionAudit;
use App\Support\SentryActivityBreadcrumbs;
use Illuminate\Database\Eloquent\Collection;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\Relation;
use Illuminate\Pagination\LengthAwarePaginator;
use Illuminate\Pagination\Paginator;
use Illuminate\Support\Facades\Log;
use Illuminate\Support\Facades\View;
use Illuminate\Support\ServiceProvider;
use Illuminate\Support\Str;
use Livewire\Livewire;

class AppServiceProvider extends ServiceProvider
{
    /**
     * Register any application services.
     *
     * @return void
     */
    public function register()
    {
        $this->app->bind(RagClientInterface::class, RagClient::class);

        // Browser-test seam only: when RAG_FAKE_STREAM is on (Dusk env), the served
        // app streams a canned SSE body instead of calling the real RAG service.
        // Flag defaults to false, so production behavior is unchanged.
        if (config('services.rag.fake_stream', false)) {
            $this->app->bind(RagClientInterface::class, CannedSseRagClient::class);
        }

        $this->app->singleton(PageContextResolverRegistry::class, function () {
            return new PageContextResolverRegistry([
                new JobsPageContextResolver,
            ]);
        });

        $this->app->scoped('audit.request_id', fn () => (string) Str::uuid());
    }

    /**
     * Bootstrap any application services.
     *
     * @return void
     */
    public function boot()
    {
        // Eloquent strictness, first step: lazy loads are reported, not thrown.
        // Many existing paths still lazy load, so a throw would break the suite;
        // the log line makes each N+1 visible so it can be eager-loaded and the
        // handler removed.
        Model::preventLazyLoading(! $this->app->isProduction());
        Model::handleLazyLoadingViolationUsing(function (Model $model, string $relation): void {
            Log::warning('Lazy-loaded relation', ['model' => $model::class, 'relation' => $relation]);
        });

        if (request()->header('X-Playwright-Visual') && $this->app->bound('debugbar')) {
            $this->app['debugbar']->disable();
        }

        // Mirror user-activity model events into Sentry breadcrumbs (inert until a DSN is set).
        SentryActivityBreadcrumbs::register();
        LivewireInteractionAudit::register();

        Livewire::component('dashboard.latest-invoices', LatestInvoicesComponent::class);
        Livewire::component('dashboard.latest-purchases', LatestPurchasesComponent::class);

        View::composer(['includes.menu', 'layouts.app'], function ($view) {
            $sidebarMenu = once(fn () => app(BuildSidebarMenu::class)());
            $view->with([
                'sidebarMenu' => $sidebarMenu,
                'sidebarActiveIds' => BuildSidebarMenu::activeIds($sidebarMenu),
            ]);
        });

        View::composer(['includes.ask-ai-panel', 'includes.topbar'], function ($view) {
            $ragEnabled = (bool) config('services.rag.enabled', false);
            $view->with('ragEnabled', $ragEnabled);
            $view->with(
                'ragAvailable',
                $ragEnabled && app(RagClientInterface::class)->isAvailable()
            );
        });

        View::composer('includes.ask-ai-panel', function ($view) {
            // The usage meter is a courtesy read: with no signed-in user or an
            // unreachable cache it stays hidden and the page still renders; the
            // fault itself is reported through the exception handler.
            $view->with('askAiCredits', auth()->check()
                ? rescue(fn () => app(AiCreditsService::class)->meterPayload(auth()->user()), null)
                : null);

            $groups = config('ask_ai.starter_prompts_by_permission', []);
            $starters = [];
            $user = auth()->user();

            foreach ($groups as $permission => $prompts) {
                if ($permission === 'default') {
                    continue;
                }
                if ($user && $user->can($permission)) {
                    foreach ($prompts as $prompt) {
                        $starters[] = $prompt;
                        if (count($starters) >= 3) {
                            break 2;
                        }
                    }
                }
            }

            if (count($starters) < 3 && isset($groups['default'])) {
                foreach ($groups['default'] as $prompt) {
                    $starters[] = $prompt;
                    if (count($starters) >= 3) {
                        break;
                    }
                }
            }

            $view->with('askAiStarters', $starters);
        });

        WorkOrder::observe(WorkOrderObserver::class);

        Relation::morphMap([
            'work_order' => WorkOrder::class,
            'invoice' => Bill::class,
        ]);

        Paginator::useBootstrapFive();
        /**
         * Paginate a standard Laravel Collection.
         *
         * @param  int  $perPage
         * @param  int  $total
         * @param  int  $page
         * @param  string  $pageName
         * @return array
         */
        Collection::macro('paginate', function ($perPage, $total = null, $page = null, $pageName = 'page') {
            $page = $page ?: LengthAwarePaginator::resolveCurrentPage($pageName);

            return new LengthAwarePaginator(
                $this->forPage($page, $perPage),
                $total ?: $this->count(),
                $perPage,
                $page,
                [
                    'path' => LengthAwarePaginator::resolveCurrentPath(),
                    'pageName' => $pageName,
                ]
            );
        });
    }
}
