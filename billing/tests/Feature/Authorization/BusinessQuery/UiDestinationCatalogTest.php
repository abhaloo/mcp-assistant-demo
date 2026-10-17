<?php

declare(strict_types=1);

namespace Tests\Feature\Authorization\BusinessQuery;

use App\Authorization\BusinessQuery\Catalog\DefaultUiDestinations;
use Database\Seeders\PermissionTableSeeder;
use Illuminate\Support\Facades\Route;
use Tests\TestCase;

class UiDestinationCatalogTest extends TestCase
{
    /**
     * Permission names created by migrations, not PermissionTableSeeder.
     *
     * @var list<string>
     */
    private const MIGRATION_CREATED_PERMISSION_NAMES = [
        // database/migrations/2025_06_27_000002_add_requires_approval_to_departments.php
        // database/migrations/2026_04_25_000001_grant_approve_job_requests_to_admins.php
        'approve job requests',
    ];

    public function test_every_destination_resolves_to_a_get_route(): void
    {
        foreach (DefaultUiDestinations::all() as $d) {
            $route = Route::getRoutes()->getByName($d->routeName);
            $this->assertNotNull($route, $d->key);
            $this->assertContains('GET', $route->methods(), $d->key);
        }
    }

    public function test_no_destination_is_a_mutating_get(): void
    {
        $names = array_map(fn ($d) => $d->routeName, DefaultUiDestinations::all());
        $this->assertEmpty(array_intersect($names, ['bill.convert', 'bill.convert.payable', 'logout']));
    }

    public function test_every_link_permission_is_seeded(): void
    {
        $known = array_merge(
            PermissionTableSeeder::permissionNames(),
            self::MIGRATION_CREATED_PERMISSION_NAMES,
        );
        foreach (DefaultUiDestinations::all() as $d) {
            $this->assertNotEmpty($d->linkPermissions, $d->key);
            foreach ($d->linkPermissions as $p) {
                $this->assertContains($p, $known, "{$d->key}: {$p}");
            }
        }
    }

    public function test_action_permissions_include_the_host_page_permissions(): void
    {
        $actions = array_filter(DefaultUiDestinations::all(), fn ($d) => $d->kind === 'action');
        $this->assertEmpty($actions);
    }

    public function test_keys_are_unique_and_labels_present(): void
    {
        $keys = array_map(fn ($d) => $d->key, DefaultUiDestinations::all());
        $this->assertSame(count($keys), count(array_unique($keys)));
        foreach (DefaultUiDestinations::all() as $d) {
            $this->assertNotSame('', trim($d->label), $d->key);
        }
    }

    public function test_actions_that_live_only_on_a_record_page_are_not_destinations(): void
    {
        foreach (DefaultUiDestinations::all() as $d) {
            $this->assertNotSame('New Payment', $d->label, $d->key);
        }
    }

    public function test_new_quotation_has_no_aliases(): void
    {
        $this->expectException(\InvalidArgumentException::class);
        $this->expectExceptionMessage('Unknown UI destination key: bill.quotation.create');
        DefaultUiDestinations::byKey('bill.quotation.create');
    }

    public function test_pending_approvals_is_a_catalog_destination(): void
    {
        $this->expectException(\InvalidArgumentException::class);
        $this->expectExceptionMessage('Unknown UI destination key: approvals.index');
        DefaultUiDestinations::byKey('approvals.index');
    }

    public function test_demo_catalog_is_only_the_three_pages(): void
    {
        $keys = array_map(fn ($d) => $d->key, DefaultUiDestinations::all());
        sort($keys);
        $this->assertSame(
            ['bill.invoices', 'customer.index', 'work-order.index'],
            $keys,
        );
        foreach (DefaultUiDestinations::all() as $d) {
            $this->assertSame('page', $d->kind, $d->key);
        }
    }
}
