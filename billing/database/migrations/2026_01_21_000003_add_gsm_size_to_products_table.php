<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    /**
     * Run the migrations.
     *
     * Adds GSM (paper weight) and Size columns to products table.
     * These are common attributes for paper products used in printing.
     */
    public function up()
    {
        Schema::table('products', function (Blueprint $table) {
            $table->string('gsm', 50)->nullable()->after('name');      // e.g., "80", "350"
            $table->string('size', 50)->nullable()->after('gsm');      // e.g., "64x90", "70x100"
        });
    }

    /**
     * Reverse the migrations.
     */
    public function down()
    {
        Schema::table('products', function (Blueprint $table) {
            $table->dropColumn(['gsm', 'size']);
        });
    }
};
