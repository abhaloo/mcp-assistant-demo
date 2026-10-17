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
        Schema::table('inventory_items', function (Blueprint $table) {
            // Costing unit entry (what user enters in costing bill)
            $table->string('costing_unit', 50)->nullable()->after('cost_breakdown')
                ->comment('Unit used for costing entry (pallet, drum, tin, etc.)');

            $table->decimal('costing_quantity', 15, 4)->nullable()->after('costing_unit')
                ->comment('Quantity in costing units entered by user');

            // Conversion factors used (copied from product, can be overridden per line)
            $table->decimal('costing_to_intermediate', 10, 4)->nullable()->after('costing_quantity')
                ->comment('Conversion factor used: costing to intermediate');

            $table->decimal('intermediate_to_stock', 10, 4)->nullable()->after('costing_to_intermediate')
                ->comment('Conversion factor used: intermediate to stock');

            // Calculated intermediate quantities (for display)
            $table->decimal('intermediate_quantity', 15, 4)->nullable()->after('intermediate_to_stock')
                ->comment('Calculated quantity in intermediate units (reams, etc.)');

            // Cost breakdown at each level (stored for audit/display)
            $table->decimal('cost_per_costing_unit', 15, 4)->nullable()->after('intermediate_quantity')
                ->comment('Landed cost per costing unit (per pallet, drum, etc.)');

            $table->decimal('cost_per_intermediate', 15, 4)->nullable()->after('cost_per_costing_unit')
                ->comment('Landed cost per intermediate unit (per ream, etc.)');

            // Phase 2: Weight-based invoicing
            $table->string('invoice_unit_type', 20)->nullable()->after('cost_per_intermediate')
                ->comment('Invoice unit type: weight or count (Phase 2)');

            $table->decimal('invoice_weight_kg', 15, 4)->nullable()->after('invoice_unit_type')
                ->comment('Invoice weight in kg if weight-based (Phase 2)');
        });
    }

    /**
     * Reverse the migrations.
     *
     * @return void
     */
    public function down()
    {
        Schema::table('inventory_items', function (Blueprint $table) {
            $table->dropColumn([
                'costing_unit',
                'costing_quantity',
                'costing_to_intermediate',
                'intermediate_to_stock',
                'intermediate_quantity',
                'cost_per_costing_unit',
                'cost_per_intermediate',
                'invoice_unit_type',
                'invoice_weight_kg',
            ]);
        });
    }
};
