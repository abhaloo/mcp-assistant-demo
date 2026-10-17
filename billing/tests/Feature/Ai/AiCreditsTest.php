<?php

declare(strict_types=1);

namespace Tests\Feature\Ai;

use App\Models\Entity;
use App\Models\User;
use App\Services\AiCreditsService;
use Illuminate\Cache\RateLimiting\Limit;
use Illuminate\Cache\RateLimiting\Unlimited;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\RateLimiter;
use Spatie\Permission\Models\Role;
use Spatie\Permission\PermissionRegistrar;
use Tests\TestCase;

class AiCreditsTest extends TestCase
{
    use RefreshDatabase;

    protected User $user;

    protected function setUp(): void
    {
        parent::setUp();

        Entity::factory()->create(['id' => 1]);
        $this->user = User::factory()->create(['entity_id' => 1, 'role' => 'user']);

        config([
            'services.rag.enabled' => true,
            'services.rag.url' => 'http://rag.test',
            'services.rag.jwt_secret' => 'credits-test-secret',
            'services.rag.credits.monthly_cap' => 1.0,
        ]);
    }

    /** @test */
    public function it_returns_402_when_monthly_cap_is_reached(): void
    {
        $credits = app(AiCreditsService::class);
        Cache::put($credits->cacheKey($this->user), 1.0, now()->endOfMonth());

        $response = $this->actingAs($this->user)
            ->postJson('/ai-search/stream', ['question' => 'budget test']);

        $response->assertStatus(402);
        $response->assertJsonStructure(['cap', 'used', 'reset_date']);
    }

    /** @test */
    public function usage_increments_from_done_event_usage(): void
    {
        $credits = app(AiCreditsService::class);
        $credits->incrementFromUsage($this->user, [
            'input_tokens' => 1000,
            'output_tokens' => 500,
        ], 'default');

        // (1000 + 500) tokens * 0.000002 default rate = 0.003 — pin the exact value.
        $this->assertEqualsWithDelta(0.003, $credits->currentUsage($this->user), 1e-9);
    }

    /** @test */
    public function ai_api_rate_limiter_is_registered_with_role_defaults(): void
    {
        $limiter = RateLimiter::limiter('ai-api');
        $this->assertNotNull($limiter);

        config(['services.rag.limits.default' => 20]);

        $request = Request::create('/ai-search/stream', 'POST');
        $request->setUserResolver(fn () => $this->user);

        $limit = $limiter($request);
        $this->assertInstanceOf(Limit::class, $limit);
        $this->assertSame(20, $limit->maxAttempts);
    }

    /** @test */
    public function ai_api_rate_limiter_is_unlimited_for_admin_role(): void
    {
        // Locks the B1 fix: services.rag.limits['admin'] === null must yield an
        // Unlimited limit, not get swallowed by the `?? 20` default.
        app(PermissionRegistrar::class)->forgetCachedPermissions();
        Role::firstOrCreate(['name' => 'admin', 'guard_name' => 'web']);

        $admin = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $admin->assignRole('admin');
        $this->assertSame('admin', $admin->fresh()->primaryRoleName());

        $request = Request::create('/ai-search/stream', 'POST');
        $request->setUserResolver(fn () => $admin->fresh());

        $limit = RateLimiter::limiter('ai-api')($request);

        $this->assertInstanceOf(Unlimited::class, $limit);
    }

    /** @test */
    public function ai_api_rate_limiter_falls_back_to_default_for_unknown_role(): void
    {
        // A role absent from services.rag.limits (no Spatie role, legacy role='staff')
        // must hit the `default` of 20 — guards the array_key_exists path.
        $staff = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $this->assertSame('staff', $staff->primaryRoleName());

        $request = Request::create('/ai-search/stream', 'POST');
        $request->setUserResolver(fn () => $staff);

        $limit = RateLimiter::limiter('ai-api')($request);

        $this->assertInstanceOf(Limit::class, $limit);
        $this->assertSame(20, $limit->maxAttempts);
    }
}
