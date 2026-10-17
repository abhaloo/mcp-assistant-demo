<?php

declare(strict_types=1);

namespace App\Authorization;

/**
 * Explicit, pure description of the query-level scoping predicates a caller
 * (an Eloquent scope, a projection query, a future RAG SQL builder) must
 * apply for one resource type + user. Plain data — no closures, no query
 * builder — so it can be serialized (e.g. into a future manifest/snapshot).
 */
final readonly class QueryScope
{
    /**
     * @param  array<string,string>  $recordPredicates  Column => required value,
     *                                                  copied from BusinessRecordDefinition::$recordPredicates (e.g. Bill subtype).
     */
    public function __construct(
        public int $entityId,
        public bool $crossEntity,
        public ?int $departmentId,
        public array $recordPredicates,
    ) {}
}
