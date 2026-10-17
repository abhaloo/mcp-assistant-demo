<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    /**
     * Run the migrations.
     *
     * @return void
     */
    public function up()
    {
        Schema::table('products', function (Blueprint $table) {
            // Conversion factors for count-based units (Phase 1)
            $table->decimal('costing_to_intermediate', 10, 4)->nullable()->after('size')
                ->comment('Conversion factor: costing unit to intermediate unit (e.g., reams per pallet)');

            $table->decimal('intermediate_to_stock', 10, 4)->nullable()->after('costing_to_intermediate')
                ->comment('Conversion factor: intermediate unit to stock unit (e.g., sheets per ream)');

            // For Phase 2: Weight conversion
            $table->decimal('kg_per_stock_unit', 15, 8)->nullable()->after('intermediate_to_stock')
                ->comment('Weight per stock unit in kg (calculated from GSM x Size for papers)');

            // Generic category attributes storage
            $table->json('category_attributes')->nullable()->after('kg_per_stock_unit')
                ->comment('Additional category-specific attributes (color, concentration, etc.)');
        });
    }

    /**
     * Reverse the migrations.
     *
     * @return void
     */
    public function down()
    {
        Schema::table('products', function (Blueprint $table) {
            $table->dropColumn([
                'costing_to_intermediate',
                'intermediate_to_stock',
                'kg_per_stock_unit',
                'category_attributes',
            ]);
        });
    }
};
