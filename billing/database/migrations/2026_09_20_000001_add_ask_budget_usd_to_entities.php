<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::table('entities', function (Blueprint $table): void {
            $table->decimal('ask_budget_usd', 12, 2)->nullable();
        });
    }

    public function down(): void
    {
        Schema::table('entities', function (Blueprint $table): void {
            $table->dropColumn('ask_budget_usd');
        });
    }
};
