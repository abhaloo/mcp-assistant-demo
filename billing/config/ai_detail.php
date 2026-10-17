<?php

declare(strict_types=1);

return [
    // Expand/dual-write is deliberately off until the B1 rollout gate is opened.
    'enabled' => (bool) env('AI_DETAIL_ENABLED', false),
    'contract' => 'typed-detail.v2',
    'compatibility_epoch' => 2,
    'families' => [
        'print.sides' => [
            'resource_type' => 'job',
            'value_kind' => 'enum',
            'revision' => 1,
            'source' => 'ai_job_detail_values',
            'fact_view' => 'ai_v2_bq_detail_attribute_fact',
            'family_aliases' => ['print side', 'print sides', 'printing side', 'printing sides'],
            'aliases' => [
                '1 side ptg',
                '1 side',
                'one side',
                '1-side',
                'single side',
                '1side',
                '2 sides ptg',
                '2 sides',
                'two sides',
                '2-side',
                'both sides',
                '2side',
                '2 sides ptg.',
            ],
        ],
        'ordered_qty' => [
            'resource_type' => 'job',
            'value_kind' => 'string',
            'revision' => 1,
            'source' => 'work_orders.ordered_qty',
            'fact_view' => 'ai_v2_bq_detail_attribute_fact',
            'family_aliases' => ['ordered quantity', 'order quantity', 'quantity ordered'],
            'aliases' => ['ordered quantity', 'quantity ordered'],
            'numeric_comparison' => 'unsupported_without_canonical_unit',
        ],
    ],
];
