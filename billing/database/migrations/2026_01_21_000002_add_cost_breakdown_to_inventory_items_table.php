<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    /**
     * Run the migrations.
     *
     * Adds cost breakdown fields to inventory_items for landed cost tracking.
     * Enables audit trail of how unit costs were calculated.
     */
    public function up()
    {
        Schema::table('inventory_items', function (Blueprint $table) {
            $table->decimal('unit_price_foreign', 15, 4)->default(0)->after('cost');
            $table->decimal('invoice_value_foreign', 15, 2)->default(0);
            $table->decimal('allocated_charges', 15, 2)->default(0);
            $table->decimal('unit_cost_tzs', 15, 4)->default(0);
            $table->json('cost_breakdown')->nullable();
        });
    }

    /**
     * Reverse the migrations.
     */
    public function down()
    {
        Schema::table('inventory_items', function (Blueprint $table) {
            $table->dropColumn([
                'unit_price_foreign',
                'invoice_value_foreign',
                'allocated_charges',
                'unit_cost_tzs',
                'cost_breakdown',
            ]);
        });
    }
};
