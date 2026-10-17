<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        $this->assertNoNullEntityIdsBeforeTightening();

        if (Schema::hasTable('ai_detail_attributes')) {
            Schema::table('ai_detail_attributes', function (Blueprint $table): void {
                $table->unsignedBigInteger('entity_id')->nullable(false)->change();
            });
            $this->dropEntityDefault('ai_detail_attributes');
        }

        if (Schema::hasTable('ai_detail_migration_ledger')) {
            Schema::table('ai_detail_migration_ledger', function (Blueprint $table): void {
                $table->unsignedBigInteger('entity_id')->nullable(false)->change();
            });
            $this->dropEntityDefault('ai_detail_migration_ledger');
        }

        if (Schema::hasTable('ai_attribute_families')) {
            if (Schema::hasIndex('ai_attribute_families', 'ai_attribute_families_name_unique')) {
                Schema::table('ai_attribute_families', function (Blueprint $table): void {
                    $table->dropUnique('ai_attribute_families_name_unique');
                });
            }
            if (! Schema::hasIndex('ai_attribute_families', 'ai_attribute_families_entity_name_unique')) {
                Schema::table('ai_attribute_families', function (Blueprint $table): void {
                    $table->unique(['entity_id', 'name'], 'ai_attribute_families_entity_name_unique');
                });
            }
            Schema::table('ai_attribute_families', function (Blueprint $table): void {
                $table->unsignedBigInteger('entity_id')->nullable(false)->change();
            });
            $this->dropEntityDefault('ai_attribute_families');
        }

        if (Schema::hasTable('ai_definition_revisions')) {
            if (Schema::hasIndex('ai_definition_revisions', 'ai_def_rev_hash_unique')) {
                Schema::table('ai_definition_revisions', function (Blueprint $table): void {
                    $table->dropUnique('ai_def_rev_hash_unique');
                });
            }
            if (! Schema::hasIndex('ai_definition_revisions', 'ai_def_rev_entity_hash_unique')) {
                Schema::table('ai_definition_revisions', function (Blueprint $table): void {
                    $table->unique(['entity_id', 'revision_hash'], 'ai_def_rev_entity_hash_unique');
                });
            }
            Schema::table('ai_definition_revisions', function (Blueprint $table): void {
                // Definition bytes are global/immutable; tenant ownership is
                // carried by entity_id and therefore scoped in this index.
                $table->unsignedBigInteger('entity_id')->nullable(false)->change();
            });
            $this->dropEntityDefault('ai_definition_revisions');
        }

        if (Schema::hasTable('ai_job_detail_values')) {
            Schema::table('ai_job_detail_values', function (Blueprint $table): void {
                $table->unsignedBigInteger('entity_id')->nullable(false)->change();
            });
            $this->dropEntityDefault('ai_job_detail_values');
        }
    }

    public function down(): void
    {
        if (Schema::hasTable('ai_detail_attributes')) {
            Schema::table('ai_detail_attributes', function (Blueprint $table): void {
                $table->unsignedBigInteger('entity_id')->default(1)->change();
            });
        }

        if (Schema::hasTable('ai_detail_migration_ledger')) {
            Schema::table('ai_detail_migration_ledger', function (Blueprint $table): void {
                $table->unsignedBigInteger('entity_id')->nullable()->change();
            });
        }

        if (Schema::hasTable('ai_attribute_families')) {
            Schema::table('ai_attribute_families', function (Blueprint $table): void {
                // Keep the entity/name scope on rollback. Once multiple
                // tenants have legitimate same-name families, restoring the
                // historical global name index would make rollback fail.
                $table->unsignedBigInteger('entity_id')->default(1)->change();
            });
        }

        if (Schema::hasTable('ai_definition_revisions')) {
            Schema::table('ai_definition_revisions', function (Blueprint $table): void {
                // Keep the scoped hash index on rollback; global uniqueness is
                // no longer truthful after two tenants publish equal bytes.
                $table->unsignedBigInteger('entity_id')->default(1)->change();
            });
        }

        if (Schema::hasTable('ai_job_detail_values')) {
            Schema::table('ai_job_detail_values', function (Blueprint $table): void {
                $table->unsignedBigInteger('entity_id')->default(1)->change();
            });
        }
    }

    private function dropEntityDefault(string $table): void
    {
        $driver = DB::connection()->getDriverName();
        if ($driver === 'mysql') {
            DB::statement("ALTER TABLE `{$table}` ALTER COLUMN `entity_id` DROP DEFAULT");
        } elseif ($driver === 'pgsql') {
            DB::statement("ALTER TABLE \"{$table}\" ALTER COLUMN \"entity_id\" DROP DEFAULT");
        }
    }

    private function assertNoNullEntityIdsBeforeTightening(): void
    {
        foreach ([
            'ai_detail_attributes',
            'ai_detail_migration_ledger',
            'ai_attribute_families',
            'ai_definition_revisions',
            'ai_job_detail_values',
        ] as $table) {
            if (! Schema::hasTable($table) || ! Schema::hasColumn($table, 'entity_id')) {
                continue;
            }

            $nullCount = DB::table($table)->whereNull('entity_id')->count();
            if ($nullCount > 0) {
                throw new RuntimeException(
                    "Cannot require {$table}.entity_id: {$nullCount} existing row(s) have NULL tenant identity. Reconcile them before applying 2026_08_23_000002.",
                );
            }
        }
    }
};
