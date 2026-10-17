<?php

declare(strict_types=1);

namespace App\Authorization\BusinessQuery\Catalog;

use App\Authorization\BusinessQuery\BucketSetDefinition;

final class DefaultBucketSets
{
    /** @return list<BucketSetDefinition> */
    public static function all(): array
    {
        return [
            new BucketSetDefinition(
                name: 'ar_aging',
                owningResource: 'invoice',
                dimension: 'due_date',
                buckets: [
                    ['Current/not due', 0],
                    ['1-'.CatalogConstants::OVERDUE_AFTER_DAYS, CatalogConstants::OVERDUE_AFTER_DAYS],
                    ['31-60', 60],
                    ['61-90', 90],
                    ['91+', null],
                ],
                appliesFilterSql: 'outstanding > 0 AND due_date IS NOT NULL',
                description: 'AR aging computed from each invoice due date relative to the request Business Date. '
                    .'Result shape [invoices_count, bill_outstanding]. NULL due dates are excluded.',
                relativeToBusinessDate: true,
                resultShape: CatalogConstants::AR_AGING_RESULT_SHAPE,
                exampleQuestions: ['How is AR aging distributed?'],
            ),
            new BucketSetDefinition(
                name: 'payment_status',
                owningResource: 'invoice',
                appliesFilterSql: 'outstanding IS NOT NULL AND items_total IS NOT NULL',
                description: 'Invoice payment status from the outstanding balance vs the invoice amount: '
                    .'Never paid (balance equals or exceeds amount), Partially paid, Fully paid (exactly 0), '
                    .'Overpaid (negative). Result shape [invoices_count]. Buckets are mutually exclusive; '
                    .'rows missing either amount are excluded.',
                bucketPredicates: [
                    ['Never paid', 'outstanding > 0 AND outstanding >= items_total'],
                    ['Partially paid', 'outstanding > 0 AND outstanding < items_total'],
                    ['Fully paid', 'outstanding = 0'],
                    ['Overpaid', 'outstanding < 0'],
                ],
                resultShape: ['invoices_count'],
                exampleQuestions: ['How do our invoices break down by payment status?'],
            ),
            new BucketSetDefinition(
                name: 'job_status',
                owningResource: 'job',
                appliesFilterSql: 'status IS NOT NULL',
                description: 'Jobs by work-order status. Statuses are mutually exclusive and cover '
                    .'every job. The active_jobs_count measure counts the Created, In progress, '
                    .'Submitted, and Pending move approval buckets; Invoiced, Finished, Delivered, '
                    .'and Cancelled are not active. Result shape [jobs_count].',
                bucketPredicates: [
                    ['Created', "status = 'CREATED'"],
                    ['In progress', "status = 'IN PROGRESS'"],
                    ['Submitted', "status = 'SUBMITTED'"],
                    ['Pending move approval', "status = 'PENDING MOVE APPROVAL'"],
                    ['Invoiced', "status = 'INVOICED'"],
                    ['Finished', "status = 'FINISHED'"],
                    ['Delivered', "status = 'DELIVERED'"],
                    ['Cancelled', "status = 'CANCELLED'"],
                ],
                resultShape: ['jobs_count'],
                exampleQuestions: ['How do our jobs break down by status?'],
            ),
        ];
    }
}
