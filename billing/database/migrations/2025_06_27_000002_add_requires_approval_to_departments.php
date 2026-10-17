<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;
use Spatie\Permission\Models\Permission;

return new class extends Migration
{
    public function up(): void
    {
        Schema::table('departments', function (Blueprint $table) {
            $table->boolean('requires_approval')->default(false)->after('name');
        });

        // Set approval requirement on departments that require admin oversight
        // ID 16 = COMPLIMENTARY&INHOUSE JOB
        // ID 14 = REJECTED & CANCELLED J/O
        DB::table('departments')
            ->whereIn('id', [14, 16])
            ->update(['requires_approval' => true]);

        // Create approval permission (idempotent - won't fail if exists)
        Permission::firstOrCreate(
            ['name' => 'approve job requests', 'guard_name' => 'web'],
            ['group' => 'approval']
        );
    }

    public function down(): void
    {
        Schema::table('departments', function (Blueprint $table) {
            $table->dropColumn('requires_approval');
        });

        // Remove the permission
        Permission::where('name', 'approve job requests')->delete();
    }
};
