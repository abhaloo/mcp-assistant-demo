<?php

declare(strict_types=1);

namespace Tests\Feature\Ai;

use App\Models\Entity;
use App\Models\User;
use App\Services\AiCreditsService;
use Firebase\JWT\JWT;
use Firebase\JWT\Key;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Http\Client\ConnectionException;
use Illuminate\Http\Client\Request;
use Illuminate\Support\Facades\Cache;
use Illuminate\Support\Facades\Http;
use Spatie\Permission\Models\Permission;
use Spatie\Permission\PermissionRegistrar;
use Tests\TestCase;

final class RestoreEvidenceTest extends TestCase
{
    use RefreshDatabase;

    private const TEST_SECRET = 'test-rag-jwt-secret-for-restore-unit-tests';

    protected function setUp(): void
    {
        parent::setUp();

        Cache::flush();

        config([
            'services.rag.enabled' => true,
            'services.rag.url' => 'http://rag.test',
            'services.rag.jwt_secret' => self::TEST_SECRET,
            'services.rag.jwt_iss' => 'laravel-billing',
            'services.rag.jwt_aud' => 'rag-service',
        ]);
    }

    public function test_client_content_is_rejected_before_rag(): void
    {
        Entity::factory()->create(['id' => 1]);
        $user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $this->actingAs($user);
        Http::fake();
        $this->postJson(route('ai-search.restore-evidence'), [
            'thread_id' => 'thread-restore',
            'references' => [['restore_ref' => str_repeat('a', 43)]],
            'rows' => [['salary' => 'FORGED_SALARY']],
            'authorized' => true,
        ])->assertUnprocessable()->assertJsonValidationErrors(['rows', 'authorized']);
        Http::assertNothingSent();
    }

    public function test_nested_extra_fields_are_rejected_before_rag(): void
    {
        Entity::factory()->create(['id' => 1]);
        $user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $this->actingAs($user);
        Http::fake();

        $this->postJson(route('ai-search.restore-evidence'), [
            'thread_id' => 'thread-restore',
            'references' => [
                [
                    'restore_ref' => str_repeat('a', 43),
                    'injected_rows' => [['salary' => 100000]],
                ],
            ],
        ])->assertUnprocessable()->assertJsonValidationErrors(['references.0.injected_rows']);

        Http::assertNothingSent();
    }

    public function test_unauthenticated_request_is_rejected(): void
    {
        Http::fake();

        $this->postJson(route('ai-search.restore-evidence'), [
            'thread_id' => 'thread-restore',
            'references' => [['restore_ref' => str_repeat('a', 43)]],
        ])->assertUnauthorized();

        Http::assertNothingSent();
    }

    public function test_validation_rejects_empty_references_and_oversized_references(): void
    {
        Entity::factory()->create(['id' => 1]);
        $user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $this->actingAs($user);
        Http::fake();

        // Empty references array
        $this->postJson(route('ai-search.restore-evidence'), [
            'thread_id' => 'thread-restore',
            'references' => [],
        ])->assertUnprocessable()->assertJsonValidationErrors(['references']);

        // Oversized references array (> 40)
        $oversized = [];
        for ($i = 0; $i < 41; $i++) {
            $oversized[] = ['restore_ref' => sprintf('%043d', $i)];
        }
        $this->postJson(route('ai-search.restore-evidence'), [
            'thread_id' => 'thread-restore',
            'references' => $oversized,
        ])->assertUnprocessable()->assertJsonValidationErrors(['references']);

        Http::assertNothingSent();
    }

    public function test_validation_rejects_invalid_restore_ref_format(): void
    {
        Entity::factory()->create(['id' => 1]);
        $user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $this->actingAs($user);
        Http::fake();

        // Size 42 (too short)
        $this->postJson(route('ai-search.restore-evidence'), [
            'thread_id' => 'thread-restore',
            'references' => [['restore_ref' => str_repeat('a', 42)]],
        ])->assertUnprocessable()->assertJsonValidationErrors(['references.0.restore_ref']);

        // Size 44 (too long)
        $this->postJson(route('ai-search.restore-evidence'), [
            'thread_id' => 'thread-restore',
            'references' => [['restore_ref' => str_repeat('a', 44)]],
        ])->assertUnprocessable()->assertJsonValidationErrors(['references.0.restore_ref']);

        // Invalid base64url characters (e.g. spaces, plus, slash)
        $this->postJson(route('ai-search.restore-evidence'), [
            'thread_id' => 'thread-restore',
            'references' => [['restore_ref' => str_repeat('a', 42).'+']],
        ])->assertUnprocessable()->assertJsonValidationErrors(['references.0.restore_ref']);

        Http::assertNothingSent();
    }

    public function test_validation_rejects_invalid_thread_id(): void
    {
        Entity::factory()->create(['id' => 1]);
        $user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $this->actingAs($user);
        Http::fake();

        // Empty thread_id
        $this->postJson(route('ai-search.restore-evidence'), [
            'thread_id' => '',
            'references' => [['restore_ref' => str_repeat('a', 43)]],
        ])->assertUnprocessable()->assertJsonValidationErrors(['thread_id']);

        // Oversized thread_id (> 64)
        $this->postJson(route('ai-search.restore-evidence'), [
            'thread_id' => str_repeat('t', 65),
            'references' => [['restore_ref' => str_repeat('a', 43)]],
        ])->assertUnprocessable()->assertJsonValidationErrors(['thread_id']);

        Http::assertNothingSent();
    }

    public function test_valid_request_forwards_strictly_thread_id_and_references_with_scope_token(): void
    {
        Entity::factory()->create(['id' => 1]);
        $user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $this->actingAs($user);

        $ref = str_repeat('b', 43);
        $expectedResponse = $this->validUpstreamResponse('thread-valid', $ref);

        Http::fake([
            '*api/restore-evidence*' => Http::response($expectedResponse, 200),
        ]);

        $response = $this->postJson(route('ai-search.restore-evidence'), [
            'thread_id' => 'thread-valid',
            'references' => [['restore_ref' => $ref]],
        ]);

        $response->assertOk();
        $response->assertJson($expectedResponse);

        Http::assertSent(function (Request $request) use ($ref) {
            if (! str_contains($request->url(), '/api/restore-evidence')) {
                return false;
            }

            // Strictly forwards thread_id and references
            $expectedData = [
                'thread_id' => 'thread-valid',
                'references' => [['restore_ref' => $ref]],
            ];
            if ($request->data() !== $expectedData) {
                return false;
            }

            // Authorization bearer with restore_evidence scope
            $auth = $request->header('Authorization')[0] ?? '';
            if (! str_starts_with($auth, 'Bearer ')) {
                return false;
            }

            $claims = (array) JWT::decode(substr($auth, 7), new Key(self::TEST_SECRET, 'HS256'));
            if (($claims['scope'] ?? null) !== 'restore_evidence') {
                return false;
            }

            // Sampled traceparent header
            if (! $request->hasHeader('traceparent')) {
                return false;
            }

            return true;
        });
    }

    public function test_revocation_reauthorizes_fresh_policy_on_every_request(): void
    {
        Entity::factory()->create(['id' => 1]);
        $user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $this->actingAs($user);

        app(PermissionRegistrar::class)->forgetCachedPermissions();
        $permission = Permission::firstOrCreate(
            ['name' => 'view invoice', 'guard_name' => 'web'],
            ['group' => 'invoice']
        );
        $user->givePermissionTo($permission);

        $ref = str_repeat('c', 43);
        $responseBody = $this->validUpstreamResponse('thread-revocation', $ref);

        Http::fake([
            '*api/restore-evidence*' => Http::response($responseBody, 200),
        ]);

        // First request: user HAS permission
        $this->postJson(route('ai-search.restore-evidence'), [
            'thread_id' => 'thread-revocation',
            'references' => [['restore_ref' => $ref]],
        ])->assertOk();

        // Revoke permission and flush cache
        $user->revokePermissionTo($permission);
        app(PermissionRegistrar::class)->forgetCachedPermissions();

        // Second request: user DOES NOT have permission
        $this->postJson(route('ai-search.restore-evidence'), [
            'thread_id' => 'thread-revocation',
            'references' => [['restore_ref' => $ref]],
        ])->assertOk();

        Http::assertSentCount(2);

        $recordedRequests = Http::recorded();
        $firstToken = substr($recordedRequests[0][0]->header('Authorization')[0], 7);
        $secondToken = substr($recordedRequests[1][0]->header('Authorization')[0], 7);

        $firstClaims = (array) JWT::decode($firstToken, new Key(self::TEST_SECRET, 'HS256'));
        $secondClaims = (array) JWT::decode($secondToken, new Key(self::TEST_SECRET, 'HS256'));

        $this->assertSame('restore_evidence', $firstClaims['scope']);
        $this->assertSame('restore_evidence', $secondClaims['scope']);
        $this->assertContains('view invoice', $firstClaims['permissions']);
        $this->assertNotContains('view invoice', $secondClaims['permissions']);
    }

    public function test_restore_route_does_not_consume_or_check_ai_credits(): void
    {
        Entity::factory()->create(['id' => 1]);
        $user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $this->actingAs($user);

        // Exhaust user monthly cap
        $credits = app(AiCreditsService::class);
        Cache::put($credits->cacheKey($user), 10.0, now()->endOfMonth());

        $ref = str_repeat('d', 43);
        Http::fake([
            '*api/restore-evidence*' => Http::response($this->validUpstreamResponse('thread-credits', $ref), 200),
        ]);

        $beforeUsage = $credits->currentUsage($user);

        // Restore evidence should not return 402 and should not fail
        $response = $this->postJson(route('ai-search.restore-evidence'), [
            'thread_id' => 'thread-credits',
            'references' => [['restore_ref' => $ref]],
        ]);

        $response->assertOk();
        $this->assertSame($beforeUsage, $credits->currentUsage($user));
    }

    public function test_upstream_timeout_returns_503_unavailable(): void
    {
        Entity::factory()->create(['id' => 1]);
        $user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $this->actingAs($user);

        Http::fake([
            '*api/restore-evidence*' => fn () => throw new ConnectionException('Upstream connection timeout'),
        ]);

        $response = $this->postJson(route('ai-search.restore-evidence'), [
            'thread_id' => 'thread-timeout',
            'references' => [['restore_ref' => str_repeat('e', 43)]],
        ]);

        $response->assertStatus(503);
        $response->assertJson(['message' => 'AI search temporarily unavailable.']);
    }

    public function test_upstream_500_error_returns_503_unavailable(): void
    {
        Entity::factory()->create(['id' => 1]);
        $user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $this->actingAs($user);

        Http::fake([
            '*api/restore-evidence*' => Http::response(['error' => 'internal_rag_crash'], 500),
        ]);

        $response = $this->postJson(route('ai-search.restore-evidence'), [
            'thread_id' => 'thread-500',
            'references' => [['restore_ref' => str_repeat('f', 43)]],
        ]);

        $response->assertStatus(503);
        $response->assertJson(['message' => 'AI search temporarily unavailable.']);
    }

    public function test_upstream_invalid_coherence_payload_returns_503_unavailable(): void
    {
        Entity::factory()->create(['id' => 1]);
        $user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $this->actingAs($user);

        // Status is denied, but payload is present (coherence violation)
        $incoherentResponse = [
            'version' => 1,
            'results' => [
                [
                    'restore_ref' => str_repeat('g', 43),
                    'status' => 'denied',
                    'reason' => 'policy_denied',
                    'payload' => ['forged' => 'rows'],
                ],
            ],
        ];

        Http::fake([
            '*api/restore-evidence*' => Http::response($incoherentResponse, 200),
        ]);

        $response = $this->postJson(route('ai-search.restore-evidence'), [
            'thread_id' => 'thread-incoherent',
            'references' => [['restore_ref' => str_repeat('g', 43)]],
        ]);

        $response->assertStatus(503);
        $response->assertJson(['message' => 'AI search temporarily unavailable.']);
    }

    public function test_rate_limiting_enforces_60_per_minute_threshold(): void
    {
        Entity::factory()->create(['id' => 1]);
        $user = User::factory()->create(['entity_id' => 1, 'role' => 'staff']);
        $this->actingAs($user);

        $ref = str_repeat('h', 43);
        Http::fake([
            '*api/restore-evidence*' => Http::response($this->validUpstreamResponse('thread-throttle', $ref), 200),
        ]);

        for ($i = 0; $i < 60; $i++) {
            $this->postJson(route('ai-search.restore-evidence'), [
                'thread_id' => 'thread-throttle',
                'references' => [['restore_ref' => $ref]],
            ])->assertOk();
        }

        // 61st request in the same minute window must be rate limited (429)
        $this->postJson(route('ai-search.restore-evidence'), [
            'thread_id' => 'thread-throttle',
            'references' => [['restore_ref' => $ref]],
        ])->assertStatus(429);
    }

    /**
     * @return array<string, mixed>
     */
    private function validUpstreamResponse(string $threadId, string $ref): array
    {
        return [
            'version' => 1,
            'results' => [
                [
                    'restore_ref' => $ref,
                    'status' => 'authorized',
                    'reason' => 'authorized',
                    'payload' => [
                        'thread_id' => $threadId,
                        'run_id' => 'run-restore-1',
                        'exchange_id' => 'ex-restore-1',
                        'answer_text' => 'Authorized restored answer.',
                        'sources' => [],
                        'citations' => ['parsed' => false, 'cited' => []],
                        'business_query' => null,
                        'presentation' => null,
                        'turn_result' => [
                            'version' => 1,
                            'outcome_type' => 'answered',
                            'completeness' => 'full',
                            'trusted' => false,
                            'selected' => [],
                            'omissions' => [],
                            'components' => [],
                        ],
                    ],
                ],
            ],
        ];
    }
}
