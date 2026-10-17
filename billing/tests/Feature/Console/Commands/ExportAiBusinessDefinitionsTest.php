<?php

declare(strict_types=1);

namespace Tests\Feature\Console\Commands;

use App\Authorization\AiAccessPolicy;
use App\Authorization\BusinessQuery\BusinessDefinitionRegistry;
use App\Authorization\BusinessQuery\BusinessDefinitionsExporter;
use App\Authorization\BusinessQuery\CapabilityEntry;
use App\Authorization\BusinessQuery\MeasureDefinition;
use App\Authorization\BusinessQuery\SemanticViews;
use App\Authorization\BusinessRecordRegistry;
use App\Authorization\PolicyManifestExporter;
use App\Models\Entity;
use App\Models\User;
use App\Services\RagClient;
use Firebase\JWT\JWT;
use Firebase\JWT\Key;
use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\Artisan;
use Tests\TestCase;

/**
 * Content-addressed business-definitions bundle export + v3 grant gates.
 */
final class ExportAiBusinessDefinitionsTest extends TestCase
{
    use RefreshDatabase;

    private const BUNDLES_DIR = __DIR__.'/../../../../database/ai-business-definitions/bundles';

    private const ARTIFACT_PATH = __DIR__.'/../../../../database/ai-business-definitions/business-definitions.json';

    private const HASH_PATH = __DIR__.'/../../../../database/ai-business-definitions/business-definitions.hash';

    private const INDEX_PATH = __DIR__.'/../../../../database/ai-business-definitions/index.json';

    /**
     * The command writes the repository's own artifacts, so each test snapshots them and puts
     * them back: a run that left index.json rewritten made every later assertion about the
     * accepted pair depend on test order.
     *
     * @var array<string, string|null>
     */
    private array $artifactSnapshot = [];

    protected function setUp(): void
    {
        parent::setUp();
        foreach ([self::ARTIFACT_PATH, self::HASH_PATH, self::INDEX_PATH] as $path) {
            $this->artifactSnapshot[$path] = is_file($path) ? (string) file_get_contents($path) : null;
        }
    }

    protected function tearDown(): void
    {
        foreach ($this->artifactSnapshot as $path => $contents) {
            if ($contents === null) {
                if (is_file($path)) {
                    unlink($path);
                }

                continue;
            }
            file_put_contents($path, $contents);
        }

        parent::tearDown();
    }

    private const PROHIBITED_EVAL_PHRASES = [
        'entity_rows order key',
        'cust-no-orders predicate',
        'fin-ar-aging',
        'jobs-finished-unbilled',
    ];

    private function bundlePathFor(string $hash): string
    {
        return self::BUNDLES_DIR.'/'.substr($hash, strlen('sha256:')).'.json';
    }

    /** @test */
    public function exporting_twice_produces_identical_bytes_and_equal_hashes(): void
    {
        $exporter = new BusinessDefinitionsExporter;

        $first = Artisan::call('ai-business-definitions:export');
        $firstBytes = file_get_contents(self::ARTIFACT_PATH);
        $firstHash = trim((string) file_get_contents(self::HASH_PATH));

        $second = Artisan::call('ai-business-definitions:export');
        $secondBytes = file_get_contents(self::ARTIFACT_PATH);
        $secondHash = trim((string) file_get_contents(self::HASH_PATH));

        $this->assertSame(0, $first);
        $this->assertSame(0, $second);
        $this->assertSame($firstBytes, $secondBytes);
        $this->assertSame($firstHash, $secondHash);
        $this->assertSame($exporter->hash(), $firstHash);
        $this->assertSame($exporter->canonicalJsonWithHash(), $firstBytes);
        $this->assertSame('id', collect($exporter->export()['dimensions'])->firstWhere('name', 'job.effective_bill_id')['format']);
    }

    /** @test */
    public function current_registry_and_generated_bundle_artifacts_exclude_eval_shaped_language(): void
    {
        $this->assertSame(0, Artisan::call('ai-business-definitions:export'));

        $artifacts = [
            'registry' => (string) file_get_contents(app_path('Authorization/BusinessQuery/BusinessDefinitionRegistry.php')),
            'current bundle' => (string) file_get_contents(self::ARTIFACT_PATH),
        ];
        foreach (glob(app_path('Authorization/BusinessQuery/Catalog/*.php')) ?: [] as $path) {
            $artifacts['catalog '.basename($path)] = (string) file_get_contents($path);
        }
        foreach (glob(self::BUNDLES_DIR.'/*.json') ?: [] as $path) {
            $artifacts['immutable bundle '.basename($path)] = (string) file_get_contents($path);
        }

        foreach ($artifacts as $name => $contents) {
            foreach (self::PROHIBITED_EVAL_PHRASES as $phrase) {
                $this->assertStringNotContainsString($phrase, $contents, "{$name}: {$phrase}");
            }
        }
    }

    /** @test */
    public function injecting_a_mutated_registry_changes_the_content_hash(): void
    {
        $defaults = BusinessDefinitionRegistry::default()->definitions();
        $measures = $defaults['measures'];
        $first = $measures[0];
        $measures[0] = new MeasureDefinition(
            name: $first->name,
            owningResource: $first->owningResource,
            sqlExpression: $first->sqlExpression,
            aggType: $first->aggType,
            filterSql: $first->filterSql,
            description: $first->description.' [mutated for hash proof]',
            format: $first->format,
            timeDimension: $first->timeDimension,
            capabilityState: $first->capabilityState,
        );

        $mutated = new BusinessDefinitionRegistry([
            'resources' => $defaults['resources'],
            'measures' => $measures,
            'dimensions' => $defaults['dimensions'],
            'joins' => $defaults['joins'],
            'bucket_sets' => $defaults['bucket_sets'],
            'segments' => $defaults['segments'],
            'capabilities' => $defaults['capabilities'],
        ]);

        $baseline = new BusinessDefinitionsExporter;
        $changed = new BusinessDefinitionsExporter($mutated);

        $this->assertNotSame($baseline->hash(), $changed->hash());
        $this->assertNotSame($baseline->canonicalJsonWithHash(), $changed->canonicalJsonWithHash());
    }

    /** @test */
    public function exported_measures_cross_validate_against_semantic_view_columns(): void
    {
        $payload = (new BusinessDefinitionsExporter)->export();
        $bindingsByName = [];
        foreach ($payload['resources'] as $resource) {
            $bindingsByName[$resource['name']] = $resource;
        }

        foreach ($payload['measures'] as $measure) {
            $binding = $bindingsByName[$measure['owning_resource']] ?? null;
            $this->assertNotNull($binding, $measure['name']);
            $view = $binding['projection_view'];
            $this->assertArrayHasKey($view, SemanticViews::COLUMNS, $measure['name']);

            $allowed = SemanticViews::COLUMNS[$view];
            foreach (BusinessDefinitionsExporter::referencedColumns($measure['sql_expression']) as $column) {
                $this->assertContains(
                    $column,
                    $allowed,
                    "measure [{$measure['name']}] sql_expression references undeclared column [{$column}] on [{$view}]",
                );
            }
            if ($measure['filter_sql'] !== null) {
                foreach (BusinessDefinitionsExporter::referencedColumns($measure['filter_sql']) as $column) {
                    $this->assertContains(
                        $column,
                        $allowed,
                        "measure [{$measure['name']}] filter_sql references undeclared column [{$column}] on [{$view}]",
                    );
                }
            }
        }

        $violations = (new BusinessDefinitionsExporter)->validateAll();
        $this->assertSame([], $violations, implode('; ', $violations));
    }

    /** @test */
    public function export_throws_when_registry_validate_is_clean_but_semantic_columns_are_not(): void
    {
        $defaults = BusinessDefinitionRegistry::default()->definitions();
        $first = $defaults['measures'][0];
        $defaults['measures'][0] = new MeasureDefinition(
            name: $first->name,
            owningResource: $first->owningResource,
            sqlExpression: 'SUM(not_a_semantic_column)',
            aggType: $first->aggType,
            filterSql: $first->filterSql,
            description: $first->description,
            format: $first->format,
            timeDimension: $first->timeDimension,
            capabilityState: $first->capabilityState,
            currencyDimension: $first->currencyDimension,
        );
        $mutated = new BusinessDefinitionRegistry($defaults);
        $exporter = new BusinessDefinitionsExporter($mutated);

        $this->assertSame([], $mutated->validate());
        $this->assertNotEmpty($exporter->validateAll());
        $this->expectException(\RuntimeException::class);
        $this->expectExceptionMessage('not_a_semantic_column');
        $exporter->export();
    }

    /** @test */
    public function decision_pending_entries_carry_empty_permissions_and_are_non_grantable(): void
    {
        $payload = (new BusinessDefinitionsExporter)->export();
        $pending = collect($payload['capabilities'])
            ->firstWhere('name', 'invoice_payment_status');

        $this->assertNotNull($pending);
        $this->assertSame('decision_pending', $pending['capability_state']);
        $this->assertSame([], $pending['required_permissions']);
        $this->assertFalse($pending['grantable']);

        config(['services.rag.snapshot_v3' => true]);

        $entity = Entity::factory()->create();
        $user = User::factory()->create(['entity_id' => $entity->id]);
        $policy = AiAccessPolicy::forUser(
            $user,
            ['view invoice', 'view journal entries', 'view quotation', 'view customer', 'view job', 'view inventory'],
            new BusinessRecordRegistry,
            BusinessDefinitionRegistry::default(),
        );

        $snapshot = $policy->snapshot(
            (new PolicyManifestExporter)->hash(),
            (new BusinessDefinitionsExporter)->hash(),
        );

        $this->assertSame(3, $snapshot['schema_version']);
        $this->assertNotContains('invoice_payment_status', $snapshot['financial_analytics']['capabilities']);
    }

    /** @test */
    public function v2_principal_fixture_has_zero_financial_analytics_capabilities(): void
    {
        config(['services.rag.snapshot_v3' => false]);

        $entity = Entity::factory()->create();
        $user = User::factory()->create(['entity_id' => $entity->id]);
        $policy = AiAccessPolicy::forUser(
            $user,
            ['view invoice', 'view journal entries'],
            new BusinessRecordRegistry,
            BusinessDefinitionRegistry::default(),
        );

        $snapshot = $policy->snapshot((new PolicyManifestExporter)->hash());

        $this->assertSame(2, $snapshot['schema_version']);
        $this->assertArrayNotHasKey('financial_analytics', $snapshot);
        $this->assertArrayNotHasKey('definition_bundle_hash', $snapshot);
        $this->assertSame([], $policy->financialAnalyticsGrants());
    }

    /** @test */
    public function v3_emission_flag_defaults_off_and_pins_schema_version_two_byte_for_byte(): void
    {
        $this->assertFalse((bool) config('services.rag.snapshot_v3'));

        $entity = Entity::factory()->create();
        $user = User::factory()->create([
            'entity_id' => $entity->id,
            'department_id' => null,
            'role' => 'staff',
        ]);
        $permissions = ['view product'];
        $manifestHash = 'sha256:'.str_repeat('b', 64);

        $policy = AiAccessPolicy::forUser($user, $permissions, new BusinessRecordRegistry);
        $snapshot = $policy->snapshot($manifestHash);

        $this->assertSame(2, $snapshot['schema_version']);

        // Pin the flag-off claim: only v2 keys, schema_version int 2, no FA fields.
        $encoded = json_encode($snapshot, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
        $decoded = json_decode($encoded, true, 512, JSON_THROW_ON_ERROR);

        $this->assertSame(
            ['schema_version', 'manifest_hash', 'entity_id', 'cross_entity', 'document_tiers', 'scope_values', 'resources'],
            array_keys($decoded),
        );
        $this->assertSame(2, $decoded['schema_version']);
        $this->assertSame($manifestHash, $decoded['manifest_hash']);
        $this->assertArrayNotHasKey('financial_analytics', $decoded);
        $this->assertArrayNotHasKey('definition_bundle_hash', $decoded);

        // Turning the flag on must change schema_version; leaving it off must not.
        config(['services.rag.snapshot_v3' => true]);
        $v3 = $policy->snapshot($manifestHash, (new BusinessDefinitionsExporter)->hash());
        $this->assertSame(3, $v3['schema_version']);
        $this->assertArrayHasKey('financial_analytics', $v3);
        $this->assertArrayHasKey('definition_bundle_hash', $v3);
    }

    /** @test */
    public function v3_snapshot_fails_closed_without_a_definition_bundle_hash(): void
    {
        config(['services.rag.snapshot_v3' => true]);

        $entity = Entity::factory()->create();
        $user = User::factory()->create(['entity_id' => $entity->id]);
        $policy = AiAccessPolicy::forUser($user, ['view invoice'], new BusinessRecordRegistry);
        $manifestHash = 'sha256:'.str_repeat('c', 64);

        $this->expectException(\InvalidArgumentException::class);
        $this->expectExceptionMessage('definition_bundle_hash');
        $policy->snapshot($manifestHash);
    }

    /** @test */
    public function v3_snapshot_fails_closed_on_empty_definition_bundle_hash(): void
    {
        config(['services.rag.snapshot_v3' => true]);

        $entity = Entity::factory()->create();
        $user = User::factory()->create(['entity_id' => $entity->id]);
        $policy = AiAccessPolicy::forUser($user, ['view invoice'], new BusinessRecordRegistry);

        $this->expectException(\InvalidArgumentException::class);
        $this->expectExceptionMessage('definition_bundle_hash');
        $policy->snapshot('sha256:'.str_repeat('d', 64), '');
    }

    /** @test */
    public function export_writes_index_and_immutable_bundle_named_by_hash(): void
    {
        $exporter = new BusinessDefinitionsExporter;
        $hash = $exporter->hash();

        $exitCode = Artisan::call('ai-business-definitions:export');

        $this->assertSame(0, $exitCode);
        $this->assertFileExists($this->bundlePathFor($hash));
        $this->assertSame($exporter->canonicalJsonWithHash(), file_get_contents($this->bundlePathFor($hash)));
        $this->assertFileExists(self::INDEX_PATH);

        $index = json_decode((string) file_get_contents(self::INDEX_PATH), true, 512, JSON_THROW_ON_ERROR);
        $this->assertSame(1, $index['schema_version']);
        $this->assertSame($hash, $index['current']);
        $this->assertContains($hash, $index['accepted']);
        $this->assertLessThanOrEqual(2, count($index['accepted']));
    }

    /** @test */
    public function export_payload_includes_compatible_policy_manifest_hashes(): void
    {
        $payload = (new BusinessDefinitionsExporter)->export();
        $manifestHash = (new PolicyManifestExporter)->hash();

        $this->assertSame('business-definitions/v1', $payload['schema_version']);
        $this->assertSame((new BusinessDefinitionsExporter)->compatibilityEpoch(), $payload['compatibility_epoch']);
        $this->assertContains($manifestHash, $payload['compatible_policy_manifest_hashes']);
        $this->assertSame('Africa/Dar_es_Salaam', $payload['business_timezone']);
    }

    /** @test */
    public function export_payload_includes_ui_destinations(): void
    {
        $payload = (new BusinessDefinitionsExporter)->export();
        $this->assertArrayHasKey('ui_destinations', $payload);

        $destinations = $payload['ui_destinations'];
        $this->assertIsList($destinations);

        $keys = array_column($destinations, 'key');
        $this->assertSame(['bill.invoices', 'customer.index', 'work-order.index'], $keys);

        $invoices = collect($destinations)->firstWhere('key', 'bill.invoices');
        $this->assertNotNull($invoices);
        $this->assertSame('/bills?type=Invoice', $invoices['href']);
        $this->assertSame(['view invoice'], $invoices['link_permissions']);

        foreach ($destinations as $destination) {
            $this->assertIsString($destination['href']);
            $this->assertStringStartsWith('/', $destination['href']);
            $this->assertFalse(str_starts_with($destination['href'], '//'));
            $this->assertStringNotContainsString('/bills/convert', $destination['href']);
        }
    }

    /** @test */
    public function export_payload_pins_the_b1_detail_definition_and_curated_source(): void
    {
        $payload = (new BusinessDefinitionsExporter)->export();

        $expectedDetailDefinitions = [
            [
                'family_key' => 'print.sides',
                'revision_hash' => 'sha256:426049d6307980a4937a4a9cf8a75eeb890206c73fb8181cf5a3a24a27409575',
                'logical_source' => 'job.print',
                'physical_source' => 'ai_v2_bq_detail_attribute_fact',
                'owner_resource' => 'job',
                'owner_column' => 'resource_id',
                'value_column' => 'typed_value',
                'value_kind' => 'enum',
                'cardinality' => 'one_to_one',
                'scope_columns' => ['entity' => 'entity_id', 'department' => 'department_id'],
                'family_aliases' => config('ai_detail.families')['print.sides']['family_aliases'],
                'aliases' => config('ai_detail.families')['print.sides']['aliases'],
                'value_mapping' => [
                    '1 side ptg' => 'one_side',
                    '1 side' => 'one_side',
                    'one side' => 'one_side',
                    '1-side' => 'one_side',
                    'single side' => 'one_side',
                    '1side' => 'one_side',
                    '2 sides ptg' => 'two_side',
                    '2 sides' => 'two_side',
                    'two sides' => 'two_side',
                    '2-side' => 'two_side',
                    'both sides' => 'two_side',
                    '2side' => 'two_side',
                    '2 sides ptg.' => 'two_side',
                ],
                'uses_union' => false,
            ], [
                'family_key' => 'ordered_qty',
                'revision_hash' => 'sha256:ae58e5c089e679b7dd9b17a6cf4d28ae64b49f96b8d5a277e8c3b0502f23865e',
                'logical_source' => 'job.ordered_qty',
                'physical_source' => 'ai_v2_bq_detail_attribute_fact',
                'owner_resource' => 'job',
                'owner_column' => 'resource_id',
                'value_column' => 'typed_value',
                'value_kind' => 'string',
                'cardinality' => 'one_to_one',
                'scope_columns' => ['entity' => 'entity_id', 'department' => 'department_id'],
                'family_aliases' => config('ai_detail.families')['ordered_qty']['family_aliases'],
                'aliases' => config('ai_detail.families')['ordered_qty']['aliases'],
                'value_mapping' => new \stdClass,
                'uses_union' => false,
            ],
        ];
        $this->assertSame(
            json_encode($expectedDetailDefinitions, JSON_THROW_ON_ERROR),
            json_encode($payload['detail_definitions'], JSON_THROW_ON_ERROR),
        );

        $this->assertSame([
            [
                'family_key' => 'print.sides',
                'projection_view' => 'ai_v2_bq_detail_attribute_fact',
                'owner_resource' => 'job',
                'owner_column' => 'resource_id',
                'grain' => 'job',
                'scope_columns' => ['entity' => 'entity_id', 'department' => 'department_id'],
                'typed_value_column' => 'typed_value',
                'display_value_column' => 'display_value',
                'columns' => SemanticViews::COLUMNS['ai_v2_bq_detail_attribute_fact'],
                'uses_union' => false,
            ], [
                'family_key' => 'ordered_qty',
                'projection_view' => 'ai_v2_bq_detail_attribute_fact',
                'owner_resource' => 'job',
                'owner_column' => 'resource_id',
                'grain' => 'job',
                'scope_columns' => ['entity' => 'entity_id', 'department' => 'department_id'],
                'typed_value_column' => 'typed_value',
                'display_value_column' => 'display_value',
                'columns' => SemanticViews::COLUMNS['ai_v2_bq_detail_attribute_fact'],
                'uses_union' => false,
            ],
        ], $payload['detail_sources']);
    }

    /** @test */
    public function v3_grants_are_least_privilege_and_empty_permissions_never_satisfy(): void
    {
        config(['services.rag.snapshot_v3' => true]);

        $defaults = BusinessDefinitionRegistry::default()->definitions();
        $capabilities = array_map(
            static function (CapabilityEntry $entry): CapabilityEntry {
                if ($entry->name === 'ledger_revenue') {
                    return new CapabilityEntry(
                        name: $entry->name,
                        kind: $entry->kind,
                        resolvesTo: $entry->resolvesTo,
                        title: $entry->title,
                        description: $entry->description,
                        exampleQuestions: $entry->exampleQuestions,
                        requiredFilters: $entry->requiredFilters,
                        requiredPermissions: [], // empty list must never grant
                        capabilityState: 'enabled',
                        meta: $entry->meta,
                    );
                }

                return $entry;
            },
            $defaults['capabilities'],
        );

        $registry = new BusinessDefinitionRegistry([
            'resources' => $defaults['resources'],
            'measures' => $defaults['measures'],
            'dimensions' => $defaults['dimensions'],
            'joins' => $defaults['joins'],
            'bucket_sets' => $defaults['bucket_sets'],
            'segments' => $defaults['segments'],
            'capabilities' => $capabilities,
        ]);

        $entity = Entity::factory()->create();
        $user = User::factory()->create(['entity_id' => $entity->id]);
        $policy = AiAccessPolicy::forUser(
            $user,
            ['view journal entries', 'view invoice'],
            new BusinessRecordRegistry,
            $registry,
        );

        $grants = $policy->financialAnalyticsGrants();
        $this->assertNotContains('ledger_revenue', $grants);
        $this->assertContains('bill_amount', $grants);
        $this->assertNotContains('invoice_payment_status', $grants);
    }

    /** @test */
    public function referenced_columns_throws_when_pcre_stripping_fails(): void
    {
        $this->expectException(\RuntimeException::class);
        $this->expectExceptionMessage('PCRE');
        BusinessDefinitionsExporter::referencedColumns("SUM(\x80outstanding)");
    }

    /** @test */
    public function stage_rag_refuses_same_hash_byte_mismatch(): void
    {
        $exporter = new BusinessDefinitionsExporter;
        $hash = $exporter->hash();
        $hex = substr($hash, strlen('sha256:'));
        $stage = sys_get_temp_dir().DIRECTORY_SEPARATOR.'bq-stage-rag-'.uniqid('', true);
        $bundlesDir = $stage.DIRECTORY_SEPARATOR.'bundles';
        $this->assertTrue(mkdir($bundlesDir, 0755, true));
        file_put_contents($bundlesDir.DIRECTORY_SEPARATOR.$hex.'.json', '{"tampered":true}');

        try {
            $exit = Artisan::call('ai-business-definitions:export', ['--stage-rag' => $stage]);
            $this->assertSame(1, $exit);
            $this->assertStringContainsString('different bytes', Artisan::output());
            $this->assertSame('{"tampered":true}', file_get_contents($bundlesDir.DIRECTORY_SEPARATOR.$hex.'.json'));
        } finally {
            @unlink($bundlesDir.DIRECTORY_SEPARATOR.$hex.'.json');
            @unlink($stage.DIRECTORY_SEPARATOR.'index.json');
            @rmdir($bundlesDir);
            @rmdir($stage);
        }
    }

    /** @test */
    public function stage_rag_rotates_the_consumer_current_to_accepted_previous(): void
    {
        $exporter = new BusinessDefinitionsExporter;
        $hash = $exporter->hash();
        $priorHash = 'sha256:'.str_repeat('e', 64);
        $stage = sys_get_temp_dir().DIRECTORY_SEPARATOR.'bq-stage-rag-'.uniqid('', true);
        $bundlesDir = $stage.DIRECTORY_SEPARATOR.'bundles';
        $this->assertTrue(mkdir($bundlesDir, 0755, true));
        file_put_contents(
            $bundlesDir.DIRECTORY_SEPARATOR.substr($priorHash, strlen('sha256:')).'.json',
            json_encode(['content_hash' => $priorHash], JSON_THROW_ON_ERROR),
        );
        file_put_contents($stage.DIRECTORY_SEPARATOR.'index.json', json_encode([
            'schema_version' => 1,
            'current' => $priorHash,
            'accepted' => [$priorHash],
        ], JSON_THROW_ON_ERROR));

        try {
            $this->assertSame(0, Artisan::call('ai-business-definitions:export', ['--stage-rag' => $stage]));
            $index = json_decode((string) file_get_contents($stage.DIRECTORY_SEPARATOR.'index.json'), true, 512, JSON_THROW_ON_ERROR);
            $this->assertSame($hash, $index['current']);
            $this->assertSame([$hash, $priorHash], $index['accepted']);
        } finally {
            @unlink($bundlesDir.DIRECTORY_SEPARATOR.substr($priorHash, strlen('sha256:')).'.json');
            @unlink($bundlesDir.DIRECTORY_SEPARATOR.substr($hash, strlen('sha256:')).'.json');
            @unlink($stage.DIRECTORY_SEPARATOR.'index.json');
            @rmdir($bundlesDir);
            @rmdir($stage);
        }
    }

    /** @test */
    public function export_does_not_accept_a_truncated_prior_bundle_as_compatible(): void
    {
        $savedIndex = is_file(self::INDEX_PATH) ? (string) file_get_contents(self::INDEX_PATH) : null;
        $priorHash = 'sha256:'.str_repeat('ab', 32);
        $priorPath = $this->bundlePathFor($priorHash);
        file_put_contents($priorPath, '{"compatibility_epoch":2,"content_hash":"'.$priorHash.'"');
        file_put_contents(self::INDEX_PATH, json_encode([
            'schema_version' => 1,
            'current' => $priorHash,
            'accepted' => [$priorHash],
        ], JSON_THROW_ON_ERROR)."\n");

        try {
            $this->assertSame(0, Artisan::call('ai-business-definitions:export'));
            $index = json_decode((string) file_get_contents(self::INDEX_PATH), true, 512, JSON_THROW_ON_ERROR);
            $this->assertNotContains($priorHash, $index['accepted']);
        } finally {
            @unlink($priorPath);
            if ($savedIndex !== null) {
                file_put_contents(self::INDEX_PATH, $savedIndex);
                Artisan::call('ai-business-definitions:export');
            }
        }
    }

    /** @test */
    public function rag_client_mints_exact_definition_bundle_hash_when_snapshot_v3_is_enabled(): void
    {
        $secret = 'test-secret-at-least-32-chars-long-12345';
        config([
            'services.rag.jwt_secret' => $secret,
            'services.rag.snapshot_v2' => true,
            'services.rag.snapshot_v3' => true,
        ]);

        $entity = Entity::factory()->create();
        $user = User::factory()->create(['entity_id' => $entity->id]);

        $exporter = new BusinessDefinitionsExporter;
        $expectedHash = $exporter->hash();

        $client = app(RagClient::class);
        $token = $client->mintToken($user);

        $claims = (array) JWT::decode($token, new Key($secret, 'HS256'));
        $this->assertArrayHasKey('record_access', $claims);
        $recordAccess = (array) $claims['record_access'];

        $this->assertSame(3, $recordAccess['schema_version']);
        $this->assertSame($expectedHash, $recordAccess['definition_bundle_hash']);
        $this->assertArrayHasKey('financial_analytics', $recordAccess);
    }
}
