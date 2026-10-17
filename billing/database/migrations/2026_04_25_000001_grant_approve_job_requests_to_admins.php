<?php

declare(strict_types=1);

use App\Models\User;
use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Facades\DB;
use Spatie\Permission\Models\Permission;
use Spatie\Permission\PermissionRegistrar;

return new class extends Migration
{
    /**
     * Backfill the "approve job requests" permission to existing admin and manager users.
     *
     * The 2025_06_27_000002 migration created the permission but never assigned it,
     * leaving the approval-notification feature silently broken — Notification::send
     * was being called with an empty recipients collection.
     *
     * This app uses direct user-permission grants (no roles), so we target by users.role.
     */
    public function up(): void
    {
        $permission = Permission::firstOrCreate(
            ['name' => 'approve job requests', 'guard_name' => 'web'],
            ['group' => 'approval']
        );

        $userIds = DB::table('users')
            ->whereIn('role', ['admin', 'manager'])
            ->pluck('id');

        $existingGrants = DB::table('model_has_permissions')
            ->where('permission_id', $permission->id)
            ->where('model_type', User::class)
            ->pluck('model_id')
            ->all();

        $rows = $userIds
            ->reject(fn ($id) => in_array($id, $existingGrants, true))
            ->map(fn ($id) => [
                'permission_id' => $permission->id,
                'model_type' => User::class,
                'model_id' => $id,
            ])
            ->all();

        if (! empty($rows)) {
            DB::table('model_has_permissions')->insert($rows);
        }

        app(PermissionRegistrar::class)->forgetCachedPermissions();
    }

    public function down(): void
    {
        $permission = Permission::where('name', 'approve job requests')->first();
        if (! $permission) {
            return;
        }

        $userIds = DB::table('users')
            ->whereIn('role', ['admin', 'manager'])
            ->pluck('id');

        DB::table('model_has_permissions')
            ->where('permission_id', $permission->id)
            ->where('model_type', User::class)
            ->whereIn('model_id', $userIds)
            ->delete();

        app(PermissionRegistrar::class)->forgetCachedPermissions();
    }
};
