<?php

declare(strict_types=1);

namespace App\Services\AskAi\PageContext;

/**
 * Closed registry of Jobs page-context profiles keyed by (version, route, kind, resource_type).
 */
final readonly class JobsContextProfile
{
    public const VERSION = 1;

    public const RESOURCE_TYPE = 'work_order';

    public const LINK_KEY = 'work_orders';

    /** @var list<string> */
    public const FIELD_ALLOWLIST = [
        'work_number',
        'title',
        'customer',
        'bill_number',
        'status',
        'department',
        'priority',
        'is_high_priority',
        'section',
        'delivery',
        'actual_delivery',
        'created_at',
        'updated_at',
    ];

    /** @var array<string, int> */
    public const FIELD_CAPS = [
        'work_number' => 64,
        'title' => 200,
        'customer' => 120,
        'bill_number' => 64,
        'status' => 64,
        'department' => 120,
        'priority' => 32,
        'is_high_priority' => 5,
        'section' => 120,
        'delivery' => 32,
        'actual_delivery' => 32,
        'created_at' => 64,
        'updated_at' => 64,
        'description_summary' => 500,
        'ordered_qty' => 255,
        'no_pages' => 255,
    ];

    /** @var list<string> */
    public const DETAIL_EXTRA_FIELDS = ['description_summary', 'ordered_qty', 'no_pages'];

    /**
     * @return list<array{
     *     route: string,
     *     kind: string,
     *     profile: string,
     *     title: string,
     *     max_records: int,
     *     exact_count: int|null,
     *     requires_scope_id: bool,
     *     scope_kind: string|null
     * }>
     */
    public static function all(): array
    {
        return [
            [
                'route' => 'work-order.index',
                'kind' => 'list',
                'profile' => 'jobs.index.v1',
                'title' => 'Jobs on this page',
                'max_records' => 100,
                'exact_count' => null,
                'requires_scope_id' => false,
                'scope_kind' => null,
                'version' => 1,
            ],
            [
                'route' => 'work-order.show',
                'kind' => 'detail',
                'profile' => 'jobs.show.v1',
                'title' => 'Job detail',
                'max_records' => 1,
                'exact_count' => 1,
                'requires_scope_id' => false,
                'scope_kind' => null,
                'version' => 1,
            ],
            [
                'route' => 'work-order.show',
                'kind' => 'detail',
                'profile' => 'jobs.show.v2',
                'title' => 'Job detail',
                'max_records' => 1,
                'exact_count' => 1,
                'requires_scope_id' => false,
                'scope_kind' => null,
                'version' => 2,
            ],
            [
                'route' => 'report.work-order',
                'kind' => 'report',
                'profile' => 'jobs.report.v1',
                'title' => 'Jobs report',
                'max_records' => 100,
                'exact_count' => null,
                'requires_scope_id' => false,
                'scope_kind' => null,
                'version' => 1,
            ],
            [
                'route' => 'customer.show',
                'kind' => 'embedded_list',
                'profile' => 'jobs.customer-list.v1',
                'title' => 'Jobs for customer',
                'max_records' => 100,
                'exact_count' => null,
                'requires_scope_id' => true,
                'scope_kind' => 'customer',
                'version' => 1,
            ],
            [
                'route' => 'bill.show',
                'kind' => 'embedded_list',
                'profile' => 'jobs.bill-list.v1',
                'title' => 'Jobs on bill',
                'max_records' => 100,
                'exact_count' => null,
                'requires_scope_id' => true,
                'scope_kind' => 'bill',
                'version' => 1,
            ],
            [
                'route' => 'customer-orders.show',
                'kind' => 'embedded_list',
                'profile' => 'jobs.customer-order-list.v1',
                'title' => 'Jobs on customer order',
                'max_records' => 100,
                'exact_count' => null,
                'requires_scope_id' => true,
                'scope_kind' => 'customer_order',
                'version' => 1,
            ],
        ];
    }

    /**
     * @return array{
     *     route: string,
     *     kind: string,
     *     profile: string,
     *     title: string,
     *     max_records: int,
     *     exact_count: int|null,
     *     requires_scope_id: bool,
     *     scope_kind: string|null,
     *     version: int
     * }|null
     */
    public static function find(int $version, string $route, string $kind, string $resourceType): ?array
    {
        if ($resourceType !== self::RESOURCE_TYPE && $resourceType !== 'job') {
            return null;
        }

        foreach (self::all() as $entry) {
            if ($entry['version'] === $version && $entry['route'] === $route && $entry['kind'] === $kind) {
                return $entry;
            }
        }

        return null;
    }

    /**
     * @return array{
     *     route: string,
     *     kind: string,
     *     profile: string,
     *     title: string,
     *     max_records: int,
     *     exact_count: int|null,
     *     requires_scope_id: bool,
     *     scope_kind: string|null,
     *     version: int
     * }|null
     */
    public static function findByProfile(int $version, string $profile): ?array
    {
        foreach (self::all() as $entry) {
            if ($entry['version'] === $version && $entry['profile'] === $profile) {
                return $entry;
            }
        }

        return null;
    }

    public static function chipLabel(string $profile, ?string $workNumber = null): string
    {
        return match ($profile) {
            'jobs.show.v1', 'jobs.show.v2' => $workNumber !== null ? 'Job #'.$workNumber : 'Job on this page',
            'jobs.report.v1' => 'Jobs report',
            'jobs.customer-list.v1' => 'Jobs for customer',
            'jobs.bill-list.v1' => 'Jobs on bill',
            'jobs.customer-order-list.v1' => 'Jobs on customer order',
            default => 'Jobs on this page',
        };
    }
}
