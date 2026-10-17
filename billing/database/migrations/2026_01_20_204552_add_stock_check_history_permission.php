<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;
use Spatie\Permission\PermissionRegistrar;

return new class extends Migration
{
    public function up()
    {
        DB::table('permissions')->updateOrInsert(
            ['name' => 'view stock check history', 'guard_name' => 'web'],
            [
                'name' => 'view stock check history',
                'group' => 'inventory',
                'guard_name' => 'web',
                'created_at' => now(),
                'updated_at' => now(),
            ]
        );
        app()[PermissionRegistrar::class]->forgetCachedPermissions();
    }

    public function down()
    {
        DB::table('permissions')->where('name', 'view stock check history')->delete();
        app()[PermissionRegistrar::class]->forgetCachedPermissions();
    }
};
