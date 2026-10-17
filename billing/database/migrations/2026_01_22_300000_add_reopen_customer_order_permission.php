<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;
use Spatie\Permission\PermissionRegistrar;

return new class extends Migration
{
    /**
     * Run the migrations.
     */
    public function up()
    {
        // Clear permission cache
        app()[PermissionRegistrar::class]->forgetCachedPermissions();

        // Check if the permission already exists
        $exists = DB::table('permissions')
            ->where('name', 'reopen customer order')
            ->exists();

        if (!$exists) {
            DB::table('permissions')->insert([
                'name' => 'reopen customer order',
                'group' => 'customer order',
                'guard_name' => 'web',
                'created_at' => now(),
                'updated_at' => now(),
            ]);
        }
    }

    /**
     * Reverse the migrations.
     */
    public function down()
    {
        DB::table('permissions')
            ->where('name', 'reopen customer order')
            ->delete();
    }
};
