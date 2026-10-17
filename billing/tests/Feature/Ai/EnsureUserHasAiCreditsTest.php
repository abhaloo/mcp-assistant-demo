<?php

declare(strict_types=1);

namespace Tests\Feature\Ai;

use App\Http\Middleware\EnsureUserHasAiCredits;
use App\Models\Entity;
use App\Models\User;
use App\Services\AiCreditsService;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Cache;
use Tests\TestCase;

class EnsureUserHasAiCreditsTest extends TestCase
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
            'services.rag.credits.monthly_cap' => 1.0,
            'services.rag.credits.upgrade_url' => 'https://example.test/upgrade',
        ]);

        Cache::flush();
    }

    private function middleware(): EnsureUserHasAiCredits
    {
        return new EnsureUserHasAiCredits(app(AiCreditsService::class));
    }

    private function requestForUser(): Request
    {
        $request = Request::create('/ai-search/stream', 'POST');
        $request->setUserResolver(fn () => $this->user);

        return $request;
    }

    /** @test */
    public function it_calls_next_when_under_cap(): void
    {
        $response = $this->middleware()->handle(
            $this->requestForUser(),
            fn ($request) => response('ok')
        );

        $this->assertSame(200, $response->getStatusCode());
        $this->assertSame('ok', $response->getContent());
    }

    /** @test */
    public function it_returns_402_with_cap_payload_when_at_cap(): void
    {
        $credits = app(AiCreditsService::class);
        Cache::put($credits->cacheKey($this->user), $credits->monthlyCap(), now()->endOfMonth());

        $response = $this->middleware()->handle(
            $this->requestForUser(),
            fn ($request) => response('ok')
        );

        $this->assertSame(402, $response->getStatusCode());

        $payload = json_decode((string) $response->getContent(), true);
        $this->assertIsArray($payload);
        $this->assertArrayHasKey('cap', $payload);
        $this->assertArrayHasKey('used', $payload);
        $this->assertArrayHasKey('reset_date', $payload);
        $this->assertArrayHasKey('upgrade_url', $payload);
    }
}
