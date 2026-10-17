<?php

namespace Database\Seeders;

use Carbon\Carbon;
use Illuminate\Database\Seeder;
use Illuminate\Support\Facades\DB;
use Spatie\Permission\PermissionRegistrar;

class PermissionTableSeeder extends Seeder
{
    /**
     * Cross-repo contract: Spatie permission names here map to document access
     * tiers in mcp-rag-qa/app/rag/access_tiers.py — renames are security-relevant.
     * See docs/superpowers/specs/2026-06-12-global-search-rag-integration-design.md §12.7
     *
     * @return list<array{name: string, group: string}>
     */
    public static function permissionDefinitions(): array
    {
        return [

            ['name' => 'system audit', 'group' => 'admin'],
            // setting
            ['name' => 'setting', 'group' => 'setting'],
            // user
            ['name' => 'view user', 'group' => 'user'],
            ['name' => 'add user', 'group' => 'user'],
            ['name' => 'delete user', 'group' => 'user'],
            ['name' => 'edit user', 'group' => 'user'],
            ['name' => 'change permission', 'group' => 'user'],
            // category
            ['name' => 'view category', 'group' => 'category'],
            ['name' => 'add category', 'group' => 'category'],
            ['name' => 'delete category', 'group' => 'category'],
            ['name' => 'edit category', 'group' => 'category'],

            // units
            ['name' => 'view unit', 'group' => 'unit'],
            ['name' => 'add unit', 'group' => 'unit'],
            ['name' => 'delete unit', 'group' => 'unit'],
            ['name' => 'edit unit', 'group' => 'unit'],

            // department
            ['name' => 'view department', 'group' => 'department'],
            ['name' => 'add department', 'group' => 'department'],
            ['name' => 'delete department', 'group' => 'department'],
            ['name' => 'edit department', 'group' => 'department'],

            // account
            ['name' => 'view account', 'group' => 'account'],
            ['name' => 'add account', 'group' => 'account'],
            ['name' => 'delete account', 'group' => 'account'],
            ['name' => 'edit account', 'group' => 'account'],
            // customer
            ['name' => 'view customer', 'group' => 'customer'],
            ['name' => 'add customer', 'group' => 'customer'],
            ['name' => 'delete customer', 'group' => 'customer'],
            ['name' => 'edit customer', 'group' => 'customer'],
            // supplier
            ['name' => 'view supplier', 'group' => 'supplier'],
            ['name' => 'add supplier', 'group' => 'supplier'],
            ['name' => 'delete supplier', 'group' => 'supplier'],
            ['name' => 'edit supplier', 'group' => 'supplier'],
            // invoice
            ['name' => 'view invoice', 'group' => 'invoice'],
            ['name' => 'add invoice', 'group' => 'invoice'],
            ['name' => 'delete invoice', 'group' => 'invoice'],
            ['name' => 'edit invoice', 'group' => 'invoice'],

            // Dashboard
            ['name' => 'full dashboard', 'group' => 'dashboard'],
            ['name' => 'job dashboard', 'group' => 'dashboard'],
            ['name' => 'inventory dashboard', 'group' => 'dashboard'],

            // quotation
            ['name' => 'view quotation', 'group' => 'quotation'],
            ['name' => 'add quotation', 'group' => 'quotation'],
            ['name' => 'delete quotation', 'group' => 'quotation'],
            ['name' => 'edit quotation', 'group' => 'quotation'],
            // accounting
            ['name' => 'add journal entry', 'group' => 'accounting'],
            ['name' => 'view journal entries', 'group' => 'accounting'],
            ['name' => 'view inventory costing', 'group' => 'accounting'],
            // job
            ['name' => 'view job', 'group' => 'job'],
            ['name' => 'add job', 'group' => 'job'],
            ['name' => 'delete job', 'group' => 'job'],
            ['name' => 'edit job', 'group' => 'job'],
            ['name' => 'move job', 'group' => 'job'],
            // customer order
            ['name' => 'view customer order', 'group' => 'customer order'],
            ['name' => 'add customer order', 'group' => 'customer order'],
            ['name' => 'edit customer order', 'group' => 'customer order'],
            ['name' => 'delete customer order', 'group' => 'customer order'],
            ['name' => 'change order details', 'group' => 'customer order'],
            ['name' => 'cancel customer order', 'group' => 'customer order'],
            ['name' => 'reopen customer order', 'group' => 'customer order'],
            ['name' => 'Edit customer order details', 'group' => 'customer order'],
            // product
            ['name' => 'view product', 'group' => 'product'],
            ['name' => 'add product', 'group' => 'product'],
            ['name' => 'delete product', 'group' => 'product'],
            ['name' => 'edit product', 'group' => 'product'],

            // inventory
            ['name' => 'view inventory', 'group' => 'inventory'],
            ['name' => 'issue item', 'group' => 'inventory'],
            ['name' => 'receive item', 'group' => 'inventory'],
            ['name' => 'approve issue', 'group' => 'inventory'],
            ['name' => 'approve receive', 'group' => 'inventory'],
            ['name' => 'delete inventory', 'group' => 'inventory'],
            ['name' => 'edit inventory', 'group' => 'inventory'],
            // tax
            ['name' => 'view tax', 'group' => 'tax'],
            ['name' => 'add tax', 'group' => 'tax'],
            ['name' => 'delete tax', 'group' => 'tax'],
            ['name' => 'edit tax', 'group' => 'tax'],
            // currency
            ['name' => 'view currency', 'group' => 'currency'],
            ['name' => 'add currency', 'group' => 'currency'],
            ['name' => 'delete currency', 'group' => 'currency'],
            ['name' => 'edit currency', 'group' => 'currency'],
            // specification
            ['name' => 'view specification', 'group' => 'specification'],
            ['name' => 'add specification', 'group' => 'specification'],
            ['name' => 'delete specification', 'group' => 'specification'],
            ['name' => 'edit specification', 'group' => 'specification'],
            // specification item
            ['name' => 'view specification item', 'group' => 'specification item'],
            ['name' => 'add specification item', 'group' => 'specification item'],
            ['name' => 'delete specification item', 'group' => 'specification item'],
            ['name' => 'edit specification item', 'group' => 'specification item'],
            // reports
            ['name' => 'outstanding receivable', 'group' => 'general report'],
            ['name' => 'payment', 'group' => 'general report'],
            ['name' => 'bill', 'group' => 'general report'],
            ['name' => 'bill overdue', 'group' => 'general report'],
            ['name' => 'inventory', 'group' => 'general report'],
            ['name' => 'summary', 'group' => 'general report'],
            ['name' => 'job', 'group' => 'general report'],
            ['name' => 'customer statement', 'group' => 'general report'],
            ['name' => 'supplier statement', 'group' => 'general report'],
            // financial report
            ['name' => 'profit and loss', 'group' => 'financial report'],
            ['name' => 'balance sheet', 'group' => 'financial report'],
            ['name' => 'account statement', 'group' => 'financial report'],
            ['name' => 'trial balance', 'group' => 'financial report'],
            ['name' => 'cash flow statement', 'group' => 'financial report'],

            // payable quotation (Task B1 / R3: canonical permission for the
            // type='Payable Quotation' AND payable='Yes' predicate — see
            // database/migrations/2026_07_21_120000_add_view_payable_quotation_permission.php)
            ['name' => 'view payable quotation', 'group' => 'supplier'],

        ];
    }

    /** @return list<string> */
    public static function permissionNames(): array
    {
        return array_column(self::permissionDefinitions(), 'name');
    }

    /**
     * Run the database seeds.
     *
     * @return void
     */
    public function run()
    {
        // Reset cached roles and permissions
        $this->command->info('truncating permissions');
        app()[PermissionRegistrar::class]->forgetCachedPermissions();
        DB::statement('SET FOREIGN_KEY_CHECKS = 0;');
        DB::table('model_has_permissions')->truncate();
        DB::table('role_has_permissions')->truncate();
        DB::table('permissions')->truncate();

        $now = Carbon::now();
        $permission = array_map(
            static fn (array $definition): array => [
                'name' => $definition['name'],
                'group' => $definition['group'],
                'guard_name' => 'web',
                'created_at' => $now,
                'updated_at' => $now,
            ],
            self::permissionDefinitions(),
        );

        DB::table('permissions')->insert($permission);
    }
}
