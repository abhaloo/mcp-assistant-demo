<?php

declare(strict_types=1);

namespace Tests\Feature\AskAi\DetailAttributes;

use Illuminate\Foundation\Testing\RefreshDatabase;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;
use Tests\TestCase;

final class EntityIdentityMigrationTest extends TestCase
{
    use RefreshDatabase;

    /** @test */
    public function typed_detail_tables_require_entity_identity_without_a_default_authority(): void
    {
        foreach ([
            'ai_detail_attributes',
            'ai_detail_migration_ledger',
            'ai_attribute_families',
            'ai_definition_revisions',
            'ai_job_detail_values',
        ] as $table) {
            $column = $this->entityColumn($table);

            $this->assertNotNull($column, "{$table}.entity_id must exist");
            if (DB::connection()->getDriverName() === 'sqlite') {
                $this->assertSame(1, (int) $column->notnull, "{$table}.entity_id must be NOT NULL");
            } else {
                $this->assertSame('NO', $column->Null, "{$table}.entity_id must be NOT NULL");
            }
            $this->assertTrue(
                ! isset($column->dflt_value) && ! isset($column->Default)
                    || (($column->dflt_value ?? $column->Default) === null),
                "{$table}.entity_id must not have a database default",
            );
        }
    }

    /** @test */
    public function migration_refuses_to_tighten_a_table_with_unreconciled_null_identity(): void
    {
        Schema::table('ai_detail_migration_ledger', function ($table): void {
            $table->unsignedBigInteger('entity_id')->nullable()->change();
        });
        DB::table('ai_detail_migration_ledger')->insert([
            'entity_id' => null,
            'resource_type' => 'job',
            'resource_id' => 99,
            'attribute_key' => 'ordered_qty',
            'status' => 'conflict',
            'created_at' => now(),
            'updated_at' => now(),
        ]);

        $migration = require base_path('database/migrations/2026_08_23_000002_require_ai_detail_entity_identity.php');

        $this->expectException(\RuntimeException::class);
        $this->expectExceptionMessage('existing row(s) have NULL tenant identity');
        $migration->up();
    }

    private function entityColumn(string $table): ?object
    {
        if (DB::connection()->getDriverName() === 'sqlite') {
            return collect(DB::select("PRAGMA table_info({$table})"))->firstWhere('name', 'entity_id');
        }

        return collect(DB::select("SHOW COLUMNS FROM {$table} WHERE Field = 'entity_id'"))->first();
    }
}
