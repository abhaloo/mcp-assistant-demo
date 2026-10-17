<?php

declare(strict_types=1);

namespace App\Authorization\BusinessQuery\Catalog;

final class CatalogConstants
{
    public const int OVERDUE_AFTER_DAYS = 30;

    /** @var list<string> */
    public const array ACTIVE_JOB_STATUSES = [
        'IN PROGRESS',
        'CREATED',
        'SUBMITTED',
        'PENDING MOVE APPROVAL',
    ];

    /** @var list<string> */
    public const array JOB_STATUSES = [
        'CREATED',
        'IN PROGRESS',
        'SUBMITTED',
        'PENDING MOVE APPROVAL',
        'INVOICED',
        'FINISHED',
        'CANCELLED',
        'DELIVERED',
    ];

    /** @var list<string> */
    public const array RESOLVABLE_TYPES = ['customer', 'product', 'department'];

    /** @var list<string> */
    public const array AR_AGING_RESULT_SHAPE = ['invoices_count', 'bill_outstanding'];

    /** @param list<string> $values */
    public static function sqlStringList(array $values): string
    {
        return implode(', ', array_map(
            static fn (string $value): string => "'{$value}'",
            $values,
        ));
    }
}
