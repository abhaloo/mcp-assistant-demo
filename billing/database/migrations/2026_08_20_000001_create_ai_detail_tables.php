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
        if (! Schema::hasTable('ai_detail_attributes')) {
            Schema::create('ai_detail_attributes', function (Blueprint $table) {
                $table->id();
                $table->unsignedBigInteger('entity_id')->default(1)->index();
                $table->string('resource_type', 64)->index();
                $table->unsignedBigInteger('resource_id')->index();
                $table->string('attribute_key', 128)->index();
                $table->string('definition_revision', 64)->nullable();
                $table->string('value_kind', 32)->default('numeric');
                $table->bigInteger('integer_value')->nullable();
                $table->decimal('numeric_value', 18, 4)->nullable();
                $table->text('string_value')->nullable();
                $table->boolean('boolean_value')->nullable();
                $table->longText('json_value')->nullable();
                $table->string('unit', 64)->nullable();
                $table->string('canonical_unit', 64)->nullable();
                $table->longText('raw_value')->nullable();
                $table->string('source_fingerprint', 64)->nullable();
                $table->timestamp('source_updated_at')->nullable();
                $table->string('migration_status', 32)->default('applied');
                $table->timestamps();

                $table->unique(
                    ['entity_id', 'resource_type', 'resource_id', 'attribute_key'],
                    'ai_detail_attr_unique'
                );
            });
        }

        if (! Schema::hasTable('ai_detail_migration_ledger')) {
            Schema::create('ai_detail_migration_ledger', function (Blueprint $table) {
                $table->id();
                $table->unsignedBigInteger('entity_id')->nullable()->index();
                $table->string('resource_type', 64)->index();
                $table->unsignedBigInteger('resource_id')->index();
                $table->string('attribute_key', 128)->index();
                $table->longText('source_raw_value')->nullable();
                $table->text('extracted_value')->nullable();
                $table->string('source_fingerprint', 64)->nullable();
                $table->string('status', 32)->default('applied');
                $table->text('reason')->nullable();
                $table->json('conflict_details')->nullable();
                $table->timestamps();
            });
        }
    }

    /**
     * Reverse the migrations.
     */
    public function down(): void
    {
        Schema::dropIfExists('ai_detail_migration_ledger');
        Schema::dropIfExists('ai_detail_attributes');
    }
};
