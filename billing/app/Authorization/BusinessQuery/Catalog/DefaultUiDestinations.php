<?php

declare(strict_types=1);

namespace App\Authorization\BusinessQuery\Catalog;

/**
 * Catalog of Ask AI page and action destinations.
 * Admin Tools / Orphan Linking stays out: /admin/orphan-linking is not guarded by route middleware.
 */
final class DefaultUiDestinations
{
    /** @return list<UiDestination> */
    public static function all(): array
    {
        return [
            new UiDestination('customer.index', 'page', 'Customers', ['Customers page'], 'customer.index', [], [], ['view customer']),
            new UiDestination('bill.invoices', 'page', 'Invoices', ['Invoices page'], 'bill.index', [], ['type' => 'Invoice'], ['view invoice']),
            new UiDestination('work-order.index', 'page', 'Jobs', ['Jobs page'], 'work-order.index', [], [], ['view job']),
        ];
    }

    public static function byKey(string $key): UiDestination
    {
        foreach (self::all() as $destination) {
            if ($destination->key === $key) {
                return $destination;
            }
        }

        throw new \InvalidArgumentException("Unknown UI destination key: {$key}");
    }
}
