<?php

declare(strict_types=1);

use App\Models\User;
use Illuminate\Database\Migrations\Migration;
use Illuminate\Support\Collection;
use Illuminate\Support\Facades\DB;
use Spatie\Permission\Models\Permission;
use Spatie\Permission\PermissionRegistrar;

/**
 * Task B1 / R3 — Payable Quotation entitlement correction.
 *
 * 1. Normalize `bills.payable` to agree with `bills.type` so the canonical
 *    fail-closed predicate `type = 'Payable Quotation' AND payable = 'Yes'`
 *    is exact. (Census against this worktree's local DB: 162 payable
 *    quotations, 0 mismatches today — this step is defensive: Livewire's
 *    BillEditorComponent binds `type` to the URL query string and, on
 *    create, writes `type` without touching `payable`
 *    (app/Http/Livewire/BillEditorComponent.php ~204,283), which is the one
 *    path that CAN produce a type-only mismatch. The only path that sets
 *    both fields together is BillController::convertPayable() ~189-195.)
 * 2. Create the explicit `view payable quotation` permission.
 * 3. Backfill it ONLY to users who, under the OLD rules, could BOTH discover
 *    a payable quotation (Global Search's old `view supplier` gate,
 *    GlobalSearchService.php ~186-198) AND open one (BillPolicy::view's old
 *    `view invoice`/`view quotation` gate ~12-23 — reachable at all only
 *    because Bill::boot()'s ambient global scope let role admin|manager
 *    bypass the payable filter, Bill.php ~172-183). That combined predicate
 *    is: role IN ('admin','manager') AND 'view supplier' AND
 *    ('view invoice' OR 'view quotation'). Everyone else loses access —
 *    search-only and detail-only fragments are intentionally tightened.
 *    The OLD gates were all `$user->can(...)` calls, which (via Spatie's
 *    `HasRoles`/`HasPermissions` traits on `App\Models\User`) resolve the
 *    UNION of direct grants (`model_has_permissions`) and role-granted
 *    permissions (`role_has_permissions` joined through `model_has_roles`)
 *    — so `userIdsWithPermission()` below queries that same union, not just
 *    direct grants, to avoid silently under-granting a user who only ever
 *    held a gating permission via a role. Future access to the NEW
 *    `view payable quotation` permission itself still comes only from this
 *    migration's direct grant (this app's own admin UI only calls
 *    `syncPermissions()`, never `assignRole()` — see
 *    2026_04_25_000001_grant_approve_job_requests_to_admins.php); the union
 *    lookup exists solely to correctly reconstruct who satisfied the OLD
 *    predicate, not to change how the new permission is granted going
 *    forward.
 */
return new class extends Migration
{
    public function up(): void
    {
        $typeOnlyFixed = DB::table('bills')
            ->where('type', 'Payable Quotation')
            ->where('payable', '!=', 'Yes')
            ->update(['payable' => 'Yes']);

        $payableOnlyFixed = DB::table('bills')
            ->where('type', '!=', 'Payable Quotation')
            ->where('payable', 'Yes')
            ->update(['payable' => 'No']);

        $canonicalCount = DB::table('bills')
            ->where('type', 'Payable Quotation')
            ->where('payable', 'Yes')
            ->count();

        $this->output(sprintf(
            'Payable Quotation normalization: %d type-only fixed, %d payable-only fixed, %d canonical rows.',
            $typeOnlyFixed,
            $payableOnlyFixed,
            $canonicalCount,
        ));

        $permission = Permission::firstOrCreate(
            ['name' => 'view payable quotation', 'guard_name' => 'web'],
            ['group' => 'supplier'],
        );

        $roleEligibleIds = DB::table('users')->whereIn('role', ['admin', 'manager'])->pluck('id');

        $discoverIds = $this->userIdsWithPermission('view supplier');
        $openIds = $this->userIdsWithPermission('view invoice')
            ->merge($this->userIdsWithPermission('view quotation'))
            ->unique();

        $beforeDiscoverCount = $roleEligibleIds->intersect($discoverIds)->count();
        $beforeOpenCount = $roleEligibleIds->intersect($openIds)->count();

        $backfillIds = $roleEligibleIds->intersect($discoverIds)->intersect($openIds)->values();

        $existingGrantIds = DB::table('model_has_permissions')
            ->where('permission_id', $permission->id)
            ->where('model_type', User::class)
            ->pluck('model_id')
            ->all();

        $rows = $backfillIds
            ->reject(fn ($id) => in_array($id, $existingGrantIds, true))
            ->map(fn ($id) => [
                'permission_id' => $permission->id,
                'model_type' => User::class,
                'model_id' => $id,
            ])
            ->all();

        if ($rows !== []) {
            DB::table('model_has_permissions')->insert($rows);
        }

        app(PermissionRegistrar::class)->forgetCachedPermissions();

        $afterHolderCount = DB::table('model_has_permissions')
            ->where('permission_id', $permission->id)
            ->where('model_type', User::class)
            ->count();

        $this->output(sprintf(
            'Payable Quotation entitlement backfill: before discover-eligible=%d, before open-eligible=%d, newly backfilled=%d, after total holders=%d.',
            $beforeDiscoverCount,
            $beforeOpenCount,
            count($rows),
            $afterHolderCount,
        ));
    }

    public function down(): void
    {
        $permission = Permission::where('name', 'view payable quotation')
            ->where('guard_name', 'web')
            ->first();

        if ($permission !== null) {
            DB::table('model_has_permissions')
                ->where('permission_id', $permission->id)
                ->where('model_type', User::class)
                ->delete();

            $permission->delete();
        }

        app(PermissionRegistrar::class)->forgetCachedPermissions();

        // Normalization is a one-way data-quality fix (bringing `payable`
        // into agreement with `type`), not a feature toggle — it is
        // deliberately not reversed here.
    }

    /**
     * Mirrors `$user->can($permissionName)` under Spatie's `HasRoles` trait:
     * the union of direct grants (`model_has_permissions`) and role-granted
     * permissions (`role_has_permissions` joined through `model_has_roles`).
     * A direct-grant-only lookup would silently under-count a user who held
     * the permission solely via a role.
     *
     * @return Collection<int, int>
     */
    private function userIdsWithPermission(string $permissionName): Collection
    {
        $permissionId = DB::table('permissions')
            ->where('name', $permissionName)
            ->where('guard_name', 'web')
            ->value('id');

        if ($permissionId === null) {
            return collect();
        }

        $directIds = DB::table('model_has_permissions')
            ->where('permission_id', $permissionId)
            ->where('model_type', User::class)
            ->pluck('model_id');

        $roleGrantedIds = DB::table('model_has_roles')
            ->join('role_has_permissions', 'model_has_roles.role_id', '=', 'role_has_permissions.role_id')
            ->where('role_has_permissions.permission_id', $permissionId)
            ->where('model_has_roles.model_type', User::class)
            ->pluck('model_has_roles.model_id');

        return $directIds->merge($roleGrantedIds)->unique();
    }

    private function output(string $message): void
    {
        // Visible in a real `php artisan migrate` run (console, non-testing
        // env). Skipped under PHPUnit (APP_ENV=testing, phpunit.xml) so
        // RefreshDatabase re-invoking this migration on every test doesn't
        // spam raw stdout writes into Artisan/PHPUnit's own output buffering
        // — `runningUnitTests()` is what actually silences it; the
        // `runningInConsole()` guard just skips non-CLI SAPIs entirely
        // (e.g. a migration ever invoked from a web request) rather than
        // writing to a stream nothing reads. php://stdout (not the CLI-only
        // STDOUT constant) keeps this safe under any SAPI that does reach it.
        if (! app()->runningInConsole() || app()->runningUnitTests()) {
            return;
        }

        $stream = fopen('php://stdout', 'wb');
        if ($stream !== false) {
            fwrite($stream, '  '.$message.PHP_EOL);
            fclose($stream);
        }
    }
};
