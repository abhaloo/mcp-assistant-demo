<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    /**
     * Run the migrations.
     */
    public function up(): void
    {
        if (! Schema::hasTable('ai_attribute_families')) {
            Schema::create('ai_attribute_families', function (Blueprint $table) {
                $table->id();
                $table->string('name', 64)->unique();
                $table->string('label', 128);
                $table->text('description')->nullable();
                $table->string('resource_type', 32)->default('job')->index();
                $table->string('value_kind', 32)->default('string');
                $table->string('status', 24)->default('published')->index();
                $table->string('sensitivity', 24)->default('internal');
                $table->unsignedBigInteger('entity_id')->default(1)->index();
                $table->timestamps();
            });
        }

        if (! Schema::hasTable('ai_definition_revisions')) {
            Schema::create('ai_definition_revisions', function (Blueprint $table) {
                $table->id();
                $table->foreignId('attribute_family_id')->constrained('ai_attribute_families')->cascadeOnDelete();
                $table->string('attribute_key', 128)->index();
                $table->string('resource_type', 32)->default('job');
                $table->string('data_type', 32)->default('string');
                $table->string('value_kind', 32)->default('string');
                $table->integer('revision')->default(1);
                $table->string('revision_hash', 71);
                $table->string('label', 128);
                $table->json('aliases')->nullable();
                $table->string('unit', 32)->nullable();
                $table->json('options')->nullable();
                $table->boolean('is_active')->default(true);
                $table->timestamp('published_at')->nullable();
                $table->timestamp('retired_at')->nullable();
                $table->unsignedBigInteger('entity_id')->default(1)->index();
                $table->json('metadata')->nullable();
                $table->timestamps();

                $table->unique(
                    ['attribute_family_id', 'attribute_key', 'revision'],
                    'ai_def_rev_family_key_rev_unique'
                );
                $table->unique('revision_hash', 'ai_def_rev_hash_unique');
            });
        }

        if (! Schema::hasTable('ai_job_detail_values')) {
            Schema::create('ai_job_detail_values', function (Blueprint $table) {
                $table->id();
                $table->foreignId('work_order_id')->constrained('work_orders')->cascadeOnDelete();
                $table->foreignId('definition_revision_id')->constrained('ai_definition_revisions')->restrictOnDelete();
                $table->string('revision_hash', 71);
                $table->unsignedBigInteger('entity_id')->default(1)->index();
                $table->unsignedBigInteger('department_id')->nullable()->index();
                $table->text('value_text')->nullable();
                $table->decimal('value_number', 18, 4)->nullable();
                $table->boolean('value_boolean')->nullable();
                $table->longText('value_json')->nullable();
                $table->text('raw_value')->nullable();
                $table->text('display_value')->nullable();
                $table->string('source', 64)->default('manual');
                $table->string('parser_version', 64)->nullable();
                $table->decimal('confidence', 5, 4)->nullable();
                $table->unsignedBigInteger('reviewer_id')->nullable()->index();
                $table->json('provenance')->nullable();
                $table->string('validation_state', 24)->default('validated');
                $table->string('source_fingerprint', 128)->nullable();
                $table->timestamp('source_updated_at')->nullable();
                $table->integer('version')->default(1);
                $table->timestamps();

                $table->unique(
                    ['work_order_id', 'definition_revision_id'],
                    'ai_job_detail_job_def_unique'
                );
                $table->index(
                    ['entity_id', 'department_id'],
                    'ai_job_detail_entity_dept_index'
                );
                $table->index(
                    ['work_order_id', 'definition_revision_id', 'entity_id', 'department_id'],
                    'ai_job_detail_owner_revision_scope_index'
                );
            });
        }
    }

    /**
     * Reverse the migrations.
     */
    public function down(): void
    {
        Schema::dropIfExists('ai_job_detail_values');
        Schema::dropIfExists('ai_definition_revisions');
        Schema::dropIfExists('ai_attribute_families');
    }
};
