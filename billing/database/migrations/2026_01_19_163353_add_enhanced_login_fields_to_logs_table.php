<?php

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
        Schema::table('logs', function (Blueprint $table) {
            $table->string('ip_address', 45)->nullable()->after('data');
            $table->string('device_type', 20)->nullable()->after('ip_address');
            $table->string('browser', 100)->nullable()->after('device_type');
            $table->string('os', 50)->nullable()->after('browser');
            $table->string('country', 100)->nullable()->after('os');
            $table->string('city', 100)->nullable()->after('country');
        });
    }

    /**
     * Reverse the migrations.
     */
    public function down(): void
    {
        Schema::table('logs', function (Blueprint $table) {
            $table->dropColumn(['ip_address', 'device_type', 'browser', 'os', 'country', 'city']);
        });
    }
};
