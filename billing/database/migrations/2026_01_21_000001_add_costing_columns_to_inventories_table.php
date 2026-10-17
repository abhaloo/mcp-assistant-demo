<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    /**
     * Run the migrations.
     *
     * Adds landed cost calculation fields to inventories table.
     *
     * Workflow:
     * 1. Store Manager creates Receive Entry (type='Receive') - simple stock entry
     * 2. Staff creates Supplier Bill (type='bill') with is_goods_costing=true
     * 3. Staff links Receive Entry(s) to Supplier Bill via supplier_bill_id
     * 4. Costing fields on Supplier Bill are used to calculate landed costs
     * 5. Calculated costs are stored in linked Receive Entry's inventory_items
     */
    public function up()
    {
        Schema::table('inventories', function (Blueprint $table) {
            // Foreign currency support
            $table->foreignId('currency_id')->nullable()->after('supplier_id')
                  ->constrained()->nullOnDelete();
            $table->string('supplier_invoice_no')->nullable()->after('reference');

            // Costing fields (on Supplier Bill - values in foreign currency)
            $table->decimal('invoice_value_foreign', 15, 2)->default(0)->after('description');
            $table->decimal('clearing_charges', 15, 2)->default(0);
            $table->decimal('freight_charges', 15, 2)->default(0);
            $table->decimal('other_charges', 15, 2)->default(0);
            $table->boolean('charges_in_tzs')->default(false);
            $table->decimal('exchange_rate', 12, 4)->default(1);
            $table->decimal('total_landed_cost_tzs', 15, 2)->default(0);

            // Flag to indicate this is a goods costing bill (vs general expense bill)
            $table->boolean('is_goods_costing')->default(false);

            // Link Receive Entry to its Supplier Bill (for costing)
            // Multiple Receive Entries can link to one Supplier Bill
            $table->foreignId('supplier_bill_id')->nullable()
                  ->constrained('inventories')->nullOnDelete();
        });
    }

    /**
     * Reverse the migrations.
     */
    public function down()
    {
        Schema::table('inventories', function (Blueprint $table) {
            $table->dropForeign(['currency_id']);
            $table->dropForeign(['supplier_bill_id']);

            $table->dropColumn([
                'currency_id',
                'supplier_invoice_no',
                'invoice_value_foreign',
                'clearing_charges',
                'freight_charges',
                'other_charges',
                'charges_in_tzs',
                'exchange_rate',
                'total_landed_cost_tzs',
                'is_goods_costing',
                'supplier_bill_id',
            ]);
        });
    }
};
