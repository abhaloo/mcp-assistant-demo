<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    /**
     * Run the migrations.
     *
     * Adds indexes to improve stock movement report query performance.
     */
    public function up()
    {
        Schema::table('inventories', function (Blueprint $table) {
            $table->index(['type', 'status', 'inventory_date'], 'idx_inv_type_status_date');
            $table->index(['supplier_id', 'inventory_date'], 'idx_inv_supplier_date');
        });

        Schema::table('inventory_items', function (Blueprint $table) {
            $table->index(['product_id', 'inventory_id'], 'idx_invitem_product_inventory');
        });
    }

    /**
     * Reverse the migrations.
     */
    public function down()
    {
        Schema::table('inventories', function (Blueprint $table) {
            $table->dropIndex('idx_inv_type_status_date');
            $table->dropIndex('idx_inv_supplier_date');
        });

        Schema::table('inventory_items', function (Blueprint $table) {
            $table->dropIndex('idx_invitem_product_inventory');
        });
    }
};
