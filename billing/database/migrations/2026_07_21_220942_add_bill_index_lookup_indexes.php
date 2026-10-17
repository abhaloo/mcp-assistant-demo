<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::table('journals', function (Blueprint $table) {
            $table->index('bill_id', 'journals_bill_id_index');
        });

        Schema::table('bills', function (Blueprint $table) {
            $table->index(['type', 'created_at'], 'bills_type_created_at_index');
        });

        Schema::table('work_orders', function (Blueprint $table) {
            $table->index('bill_id', 'work_orders_bill_id_index');
        });
    }

    public function down(): void
    {
        Schema::table('journals', function (Blueprint $table) {
            $table->dropIndex('journals_bill_id_index');
        });

        Schema::table('bills', function (Blueprint $table) {
            $table->dropIndex('bills_type_created_at_index');
        });

        Schema::table('work_orders', function (Blueprint $table) {
            $table->dropIndex('work_orders_bill_id_index');
        });
    }
};
