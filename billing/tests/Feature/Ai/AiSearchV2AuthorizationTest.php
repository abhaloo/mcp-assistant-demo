<?php

declare(strict_types=1);

namespace Tests\Feature\Ai;

use App\Models\Entity;
use App\Models\User;
use App\Services\RagClientInterface;
use GuzzleHttp\Psr7\Response;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Http\JsonResponse;
use Illuminate\Support\Facades\Cache;
use Psr\Http\Message\ResponseInterface;
use Tests\Feature\Ai\Concerns\RendersAppLayout;
use Tests\Support\FakeRagStreamClient;
use Tests\Support\RagClientStub;
use Tests\TestCase;

final class AiSearchV2AuthorizationTest extends TestCase
{
    use RefreshDatabase;
    use RendersAppLayout;

    protected function setUp(): void
    {
        parent::setUp();

        Cache::flush();

        Entity::factory()->create(['id' => 1]);

        config([
            'services.rag.enabled' => true,
            'services.rag.url' => 'http://rag.test',
            'services.rag.jwt_secret' => 'authz-test-secret',
            'ask_ai.ask_v2_enabled' => true,
        ]);
    }

    /**
     * AC-10: with the flag on, the layout renders the v2 stream URL and
     * loads BOTH v2 assets (CSS and JS) — never the legacy ones. This fails
     * if either v2 asset is absent from the rendered HTML.
     */
    public function test_flag_on_loads_v2_route_and_assets(): void
    {
        $user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $this->actingAs($user);

        config([
            'services.rag.ask_v2_enabled' => true,
            'ask_ai.ask_v2_enabled' => true,
        ]);

        $html = $this->renderAppLayout();

        $this->assertStringContainsString(
            'data-stream-url="'.route('ai-search.v2.stream').'"',
            $html,
            'Flag-on Blade must render the v2 panel stream URL.'
        );
        $this->assertStringContainsString('data-v2-enabled="1"', $html);

        // Both v2 assets must load — the test fails if either is absent.
        $this->assertStringContainsString(
            'css/mcp-ask-v2.css',
            $html,
            'Flag-on layout must load the v2 stylesheet.'
        );
        $this->assertStringContainsString(
            'resources/js/ask_ai_v2/index.js',
            $html,
            'Flag-on layout must load the v2 script via the Vite entry point.'
        );

        // The panel is the same surface in both modes, so its own assets load
        // whatever the flag says. v2 adds a transport and optional modules on
        // top; it never replaces the panel.
        $this->assertStringContainsString('js/mcp-ask-ai.js', $html);
        $this->assertStringContainsString('js/mcp-ask-ai-stream-ui.js', $html);
        $this->assertStringContainsString('js/mcp-ask-stream-client.js', $html);
        $this->assertStringContainsString('css/mcp-ask-ai.css', $html);
        $this->assertStringContainsString('css/mcp-ask-ai-composer.css', $html);
        $this->assertStringContainsString('css/mcp-dropdown.css', $html);
        $this->assertStringContainsString('js/mcp-dropdown.js', $html);

        // Every optional v2 surface reaches the browser as a flag it can read,
        // so one can be switched off without a deploy.
        $this->assertStringContainsString('data-v2-modules', $html);
    }

    /**
     * AC-10 mirror: with the flag off, the layout keeps loading only the
     * legacy assets and the legacy stream URL — a previous Billing
     * deployment never needs to understand v2 assets.
     */
    public function test_flag_off_loads_legacy_route_and_assets(): void
    {
        $user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $this->actingAs($user);

        config([
            'services.rag.ask_v2_enabled' => false,
            'ask_ai.ask_v2_enabled' => false,
        ]);

        $html = $this->renderAppLayout();

        $this->assertStringContainsString(
            'data-stream-url="'.route('ai-search.stream').'"',
            $html
        );
        $this->assertStringContainsString('data-v2-enabled="0"', $html);
        $this->assertStringContainsString('js/mcp-ask-ai.js', $html);
        $this->assertStringContainsString('js/mcp-ask-ai-stream-ui.js', $html);
        $this->assertStringContainsString('js/mcp-ask-stream-client.js', $html);
        $this->assertStringContainsString('js/mcp-dropdown.js', $html);
        $this->assertStringContainsString('css/mcp-ask-ai.css', $html);
        $this->assertStringContainsString('css/mcp-dropdown.css', $html);

        $this->assertStringNotContainsString('css/mcp-ask-v2.css', $html);
        $this->assertStringNotContainsString('js/mcp-ask-v2.js', $html);
    }

    /**
     * AC-04: a clarification/pagination submission is read-only and carries
     * no mutation or generic-action payload, and it re-runs Billing's own
     * authorization on every submission — the JWT/policy snapshot forwarded
     * with a later, read-only follow-up reflects the user's CURRENT role,
     * never a value cached from the original turn.
     */
    public function test_v2_read_only_submission_reauthorizes_policy(): void
    {
        $user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $this->actingAs($user);

        $turnOutcomeBody = implode("\n", [
            'event: turn_outcome',
            'data: {"outcome_type":"answered","trusted":true}',
            '',
            '',
        ]);
        $fakeClient = new FakeRagStreamClient($turnOutcomeBody);
        $this->app->instance(RagClientInterface::class, $fakeClient);

        $clarifyPayload = [
            'protocol_version' => '2',
            'operation' => 'clarification_reply',
            'run_id' => str_repeat('a', 32),
            'thread_id' => 'thread-authz',
            'continuation_ref' => 'opaque-continuation-ref',
            'clarification_choice_id' => 'choice-1',
            'idempotency_key' => 'idemp-authz-1',
            'response_policy' => 'strict',
        ];

        $this->postJson('/api/ai/ask/v2', $clarifyPayload)->assertOk();

        $capturedPayload = $fakeClient->lastStreamV2Payload;
        $this->assertNotNull($capturedPayload);
        foreach (['action', 'mutation', 'selector', 'ability'] as $forbiddenKey) {
            $this->assertArrayNotHasKey(
                $forbiddenKey,
                $capturedPayload,
                "A read-only clarification submission must never carry a \"{$forbiddenKey}\" field."
            );
        }
        $this->assertSame('staff', $fakeClient->lastStreamV2User->primaryRoleName());

        // The user's role changes between the original turn and this
        // read-only pagination follow-up on the SAME thread.
        $user->update(['role' => 'admin']);

        $pagePayload = [
            'protocol_version' => '2',
            'operation' => 'result_page',
            'run_id' => str_repeat('b', 32),
            'thread_id' => 'thread-authz',
            'result_page_cursor' => 'opaque-cursor',
            'idempotency_key' => 'idemp-authz-2',
            'response_policy' => 'strict',
        ];

        $this->postJson('/api/ai/ask/v2', $pagePayload)->assertOk();

        // Re-authorized fresh: reflects the CURRENT role, not a snapshot
        // cached from the first submission.
        $this->assertSame('admin', $fakeClient->lastStreamV2User->primaryRoleName());
        foreach (['action', 'mutation', 'selector', 'ability'] as $forbiddenKey) {
            $this->assertArrayNotHasKey($forbiddenKey, $fakeClient->lastStreamV2Payload);
        }
    }

    /**
     * AC-04: an upstream rejection for an expired or role-mismatched
     * continuation fails closed BEFORE any stream/execution — Billing never
     * fabricates a success response for it.
     */
    public function test_expired_continuation_is_rejected_before_execution(): void
    {
        $user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $this->actingAs($user);

        $rejectingClient = new class extends RagClientStub
        {
            /** @var array<string, mixed>|null */
            public ?array $lastStreamV2Payload = null;

            public function streamV2(array $payload, User $user): ResponseInterface
            {
                $this->lastStreamV2Payload = $payload;

                return new Response(409, [], (string) json_encode([
                    'error' => 'continuation_expired',
                ]));
            }
        };
        $this->app->instance(RagClientInterface::class, $rejectingClient);

        $response = $this->postJson('/api/ai/ask/v2', [
            'protocol_version' => '2',
            'operation' => 'clarification_reply',
            'run_id' => str_repeat('c', 32),
            'thread_id' => 'thread-authz-expired',
            'continuation_ref' => 'expired-continuation-ref',
            'clarification_choice_id' => 'choice-1',
            'idempotency_key' => 'idemp-authz-3',
            'response_policy' => 'strict',
        ]);

        $response->assertStatus(502);
        $this->assertInstanceOf(JsonResponse::class, $response->baseResponse);
        $this->assertNotNull($rejectingClient->lastStreamV2Payload);
    }

    public function test_panel_advertises_the_interaction_card_module(): void
    {
        config([
            'services.rag.ask_v2_enabled' => true,
            'ask_ai.ask_v2_enabled' => true,
            'ask_ai.v2_modules.interaction_card' => true,
        ]);

        $html = $this->renderAppLayout();
        $modules = json_decode($this->panelAttribute($html, 'data-v2-modules'), true, flags: JSON_THROW_ON_ERROR);

        $this->assertTrue($modules['interaction_card']);
    }
}
