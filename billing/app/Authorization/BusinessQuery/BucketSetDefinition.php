<?php

declare(strict_types=1);

namespace App\Authorization\BusinessQuery;

use InvalidArgumentException;

/**
 * Named bucket family in one of two shapes. Threshold: ordered [label, upper
 * bound] pairs over one dimension (aging). Categorical: [label, predicateSql]
 * pairs, one bucket per mutually-exclusive predicate (payment status).
 * Exactly one shape per set. Only bucket data is exported; caseSql() is local.
 */
readonly class BucketSetDefinition
{
    /**
     * @param  list<array{0: string, 1: int|null}>  $buckets  ordered [label, upperBoundDays|null]
     * @param  list<array{0: string, 1: string}>  $bucketPredicates  [label, predicateSql]
     * @param  list<string>  $resultShape  measure names the card advertises for this set
     * @param  list<string>  $exampleQuestions
     */
    public function __construct(
        public string $name,
        public string $owningResource,
        public string $appliesFilterSql,
        public string $description,
        public string $dimension = '',
        public array $buckets = [],
        public bool $relativeToBusinessDate = false,
        public array $bucketPredicates = [],
        public array $resultShape = [],
        public array $exampleQuestions = [],
    ) {
        if (($this->buckets === []) === ($this->bucketPredicates === [])) {
            throw new InvalidArgumentException(
                "bucket set [{$this->name}]: exactly one of buckets or bucketPredicates must be non-empty.",
            );
        }
        if ($this->buckets !== [] && $this->dimension === '') {
            throw new InvalidArgumentException(
                "bucket set [{$this->name}]: threshold buckets require a dimension.",
            );
        }
        if ($this->bucketPredicates !== [] && ($this->dimension !== '' || $this->relativeToBusinessDate)) {
            throw new InvalidArgumentException(
                "bucket set [{$this->name}]: categorical buckets take no dimension and no business-date anchor.",
            );
        }
    }

    /**
     * Dialect-free CASE over the ordered bounds.
     *
     * The bounds are day counts, not the raw dimension value, so a
     * relativeToBusinessDate set (bounds relative to "today") must be given the
     * caller's own day-count expression — the declared dimension itself (e.g. a
     * raw due_date column) is not comparable to those bounds.
     */
    public function caseSql(?string $dayCountExpr = null): string
    {
        if ($this->bucketPredicates !== []) {
            // Categorical: one WHEN per predicate, no ELSE - a row matching no
            // predicate is unbucketed, mirroring the RAG compiler.
            $parts = ['CASE'];
            foreach ($this->bucketPredicates as [$label, $predicate]) {
                $parts[] = "WHEN {$predicate} THEN '{$label}'";
            }
            $parts[] = 'END';

            return implode(' ', $parts);
        }

        if ($this->relativeToBusinessDate && $dayCountExpr === null) {
            throw new InvalidArgumentException(
                "bucket set [{$this->name}] is relative to the Business Date; caseSql() requires a day-count expression.",
            );
        }

        $expr = $dayCountExpr ?? $this->dimension;

        $parts = ['CASE'];
        $lastLabel = null;

        foreach ($this->buckets as [$label, $bound]) {
            $lastLabel = $label;
            if ($bound === null) {
                continue;
            }
            $parts[] = "WHEN {$expr} <= {$bound} THEN '{$label}'";
        }

        $parts[] = "ELSE '{$lastLabel}' END";

        return implode(' ', $parts);
    }
}
