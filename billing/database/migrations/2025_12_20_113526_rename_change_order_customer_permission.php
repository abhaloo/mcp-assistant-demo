<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;
use Spatie\Permission\PermissionRegistrar;

return new class extends Migration
{
    public function up()
    {
        DB::table('permissions')
            ->where('name', 'change order customer')
            ->update(['name' => 'change order details']);

        app()[PermissionRegistrar::class]->forgetCachedPermissions();
    }

    public function down()
    {
        DB::table('permissions')
            ->where('name', 'change order details')
            ->update(['name' => 'change order customer']);

        app()[PermissionRegistrar::class]->forgetCachedPermissions();
    }
};
