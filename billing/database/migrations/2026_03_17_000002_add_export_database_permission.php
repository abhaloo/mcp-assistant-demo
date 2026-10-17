<?php

use Illuminate\Database\Migrations\Migration;
use Spatie\Permission\Models\Permission;
use Spatie\Permission\PermissionRegistrar;

return new class extends Migration
{
    public function up(): void
    {
        app()[PermissionRegistrar::class]->forgetCachedPermissions();

        Permission::firstOrCreate([
            'name' => 'export database',
            'guard_name' => 'web',
        ], [
            'group' => 'admin',
        ]);
    }

    public function down(): void
    {
        Permission::where('name', 'export database')->delete();
    }
};
