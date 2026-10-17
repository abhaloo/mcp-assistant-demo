<?php

declare(strict_types=1);

namespace App\Authorization\BusinessQuery;

/**
 * Declared join between two resources (fan-out classification input).
 */
readonly class JoinDefinition
{
    /**
     * @param  'one_to_one'|'one_to_many'|'many_to_one'  $relationship
     * @param  bool  $optional  True when the child ("to") side's FK column is nullable — rows on
     *                          that side can exist without a parent match, so an INNER join across
     *                          this edge can silently exclude them from an aggregate. Consumers that
     *                          select a measure owned by the child side of an optional edge must
     *                          refuse rather than answer with an undercounted total.
     */
    public function __construct(
        public string $fromResource,
        public string $toResource,
        public string $relationship,
        public string $onSql,
        public bool $optional = false,
    ) {}
}
