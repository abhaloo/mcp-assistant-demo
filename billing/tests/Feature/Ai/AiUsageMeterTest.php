<?php

declare(strict_types=1);

namespace Tests\Feature\Ai;

use App\Models\Entity;
use App\Models\User;
use App\Services\AiCreditsService;
use Illuminate\Cache\ArrayStore;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\Cache;
use Tests\Feature\Ai\Concerns\RendersAppLayout;
use Tests\TestCase;

/**
 * The usage meter under the composer shows the person their own monthly Ask AI
 * spend: the panel carries the current figure on first paint, and a small
 * endpoint hands back the figure after a turn has been charged.
 */
final class AiUsageMeterTest extends TestCase
{
    use RefreshDatabase;
    use RendersAppLayout;

    private User $user;

    protected function setUp(): void
    {
        parent::setUp();

        Cache::flush();

        Entity::factory()->create(['id' => 1]);
        $this->user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);

        config([
            'services.rag.enabled' => true,
            'services.rag.url' => 'http://rag.test',
            'services.rag.jwt_secret' => 'meter-test-secret',
            'services.rag.credits.monthly_cap' => 1.0,
        ]);
    }

    public function test_the_credits_endpoint_returns_the_callers_usage_as_a_percentage(): void
    {
        app(AiCreditsService::class)->incrementUsd($this->user, 0.124);

        $response = $this->actingAs($this->user)->getJson(route('ai-search.credits'));

        $response->assertOk();
        $response->assertExactJson([
            'pct' => 12.4,
            'reset_date' => now()->startOfMonth()->addMonth()->toDateString(),
        ]);
    }

    public function test_the_reset_day_is_the_first_of_next_month_even_on_the_31st(): void
    {
        Carbon::setTestNow('2026-01-31 10:00:00');

        try {
            $response = $this->actingAs($this->user)->getJson(route('ai-search.credits'));
            $response->assertJsonPath('reset_date', '2026-02-01');
        } finally {
            Carbon::setTestNow();
        }
    }

    public function test_the_default_monthly_cap_is_one_dollar(): void
    {
        $defaults = require base_path('config/services.php');

        $this->assertSame(1.0, $defaults['rag']['credits']['monthly_cap'], 'a person with no cap set gets one dollar a month');
    }

    public function test_a_guest_page_carries_no_figure_and_still_renders(): void
    {
        $html = $this->renderAppLayout();

        $this->assertStringContainsString('id="mcp-ask-ai-root"', $html);
        $this->assertStringNotContainsString('data-credits=', $html);
        $this->assertStringContainsString('data-mcp-ask-usage', $html);
    }

    public function test_a_cache_fault_hides_the_figure_and_still_renders(): void
    {
        // A store that faults on the credits key only; the rest of the page keeps its cache.
        Cache::extend('faulty', fn () => Cache::repository(new class extends ArrayStore
        {
            public function get($key)
            {
                if (str_starts_with((string) $key, 'ai-credits:')) {
                    throw new \RuntimeException('cache down');
                }

                return parent::get($key);
            }
        }));
        config(['cache.stores.faulty' => ['driver' => 'faulty'], 'cache.default' => 'faulty']);

        $html = $this->actingAs($this->user)->renderAppLayout();

        $this->assertStringContainsString('id="mcp-ask-ai-root"', $html);
        $this->assertStringNotContainsString('data-credits=', $html);
    }
}
