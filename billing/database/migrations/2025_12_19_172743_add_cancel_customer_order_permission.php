<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;
use Spatie\Permission\PermissionRegistrar;

return new class extends Migration
{
    public function up()
    {
        DB::table('permissions')->updateOrInsert(
            ['name' => 'cancel customer order', 'guard_name' => 'web'],
            [
                'name' => 'cancel customer order',
                'group' => 'customer order',
                'guard_name' => 'web',
                'created_at' => now(),
                'updated_at' => now(),
            ]
        );

        app()[PermissionRegistrar::class]->forgetCachedPermissions();
    }

    public function down()
    {
        DB::table('permissions')->where('name', 'cancel customer order')->delete();
        app()[PermissionRegistrar::class]->forgetCachedPermissions();
    }
};
