<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up()
    {
        if (!Schema::hasTable('customer_orders')) {
            Schema::create('customer_orders', function (Blueprint $table) {
                $table->id();
                $table->string('order_number')->unique();
                $table->foreignId('customer_id')->constrained()->cascadeOnDelete();
                $table->foreignId('entity_id')->constrained()->cascadeOnDelete();
                $table->text('description')->nullable();
                $table->string('status')->default('CREATED');
                $table->timestamps();
            });
        }

        if (!Schema::hasColumn('work_orders', 'customer_order_id')) {
            Schema::table('work_orders', function (Blueprint $table) {
                $table->foreignId('customer_order_id')->nullable()->after('bill_id')
                    ->constrained('customer_orders')->nullOnDelete();
            });
        }

        if (!Schema::hasColumn('bills', 'customer_order_id')) {
            Schema::table('bills', function (Blueprint $table) {
                $table->foreignId('customer_order_id')->nullable()->after('customer_id')
                    ->constrained('customer_orders')->nullOnDelete();
            });
        }
    }

    public function down()
    {
        if (Schema::hasColumn('bills', 'customer_order_id')) {
            Schema::table('bills', function (Blueprint $table) {
                $table->dropConstrainedForeignId('customer_order_id');
            });
        }

        if (Schema::hasColumn('work_orders', 'customer_order_id')) {
            Schema::table('work_orders', function (Blueprint $table) {
                $table->dropConstrainedForeignId('customer_order_id');
            });
        }

        Schema::dropIfExists('customer_orders');
    }
};
