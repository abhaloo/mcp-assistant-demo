<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;
use Spatie\Permission\PermissionRegistrar;

return new class extends Migration
{
    public function up()
    {
        $permissions = [
            ['name' => 'view customer order', 'group' => 'customer order', 'guard_name' => 'web', 'created_at' => now(), 'updated_at' => now()],
            ['name' => 'add customer order', 'group' => 'customer order', 'guard_name' => 'web', 'created_at' => now(), 'updated_at' => now()],
            ['name' => 'edit customer order', 'group' => 'customer order', 'guard_name' => 'web', 'created_at' => now(), 'updated_at' => now()],
            ['name' => 'delete customer order', 'group' => 'customer order', 'guard_name' => 'web', 'created_at' => now(), 'updated_at' => now()],
        ];

        foreach ($permissions as $permission) {
            DB::table('permissions')->updateOrInsert(
                ['name' => $permission['name'], 'guard_name' => $permission['guard_name']],
                $permission
            );
        }

        app()[PermissionRegistrar::class]->forgetCachedPermissions();
    }

    public function down()
    {
        DB::table('permissions')->where('group', 'customer order')->delete();
        app()[PermissionRegistrar::class]->forgetCachedPermissions();
    }
};
