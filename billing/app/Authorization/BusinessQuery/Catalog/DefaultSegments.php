<?php

declare(strict_types=1);

namespace App\Authorization\BusinessQuery\Catalog;

use App\Authorization\BusinessQuery\SegmentDefinition;

final class DefaultSegments
{
    /** @return list<SegmentDefinition> */
    public static function all(): array
    {
        return [
            new SegmentDefinition(
                name: 'invoice.unpaid',
                owningResource: 'invoice',
                predicateSql: 'outstanding > 0',
                description: 'Invoices with any balance owing.',
                capabilityState: 'enabled',
                title: 'Unpaid invoices',
            ),
        ];
    }
}
