<?php

declare(strict_types=1);

namespace App\Authorization\Projections;

use App\Authorization\BusinessRecordDefinition;

/**
 * Task B5 / R1 — the authorization/scope columns a projection view must carry
 * BEYOND its manifest `readable_fields`, derived purely from the registry's
 * BusinessRecordDefinition (never a hand-copied per-resource list, so it can
 * never silently drift from the registry the way a second inventory could).
 *
 * These columns exist so RAG's executor can bind request-scoped predicates —
 * they are never answer fields, and entity/department scoping is NOT baked
 * into the view's WHERE clause (only the subtype discriminators are, per the
 * Core design). Used independently by both ProjectionDefinitions (to build
 * each view's SELECT list) and its completeness test (to compute the
 * expected column set from the registry) so a bug in one is not mirrored by
 * the other.
 */
final class ProjectionScopeColumns
{
    private function __construct() {}

    /** @return list<string> */
    public static function forDefinition(BusinessRecordDefinition $definition): array
    {
        // entity_id always: the resource's own column for standard/department
        // scope, or (for via_parent resources like credit_note, which have no
        // entity_id of their own) the parent's entity_id, joined in.
        $columns = ['entity_id'];

        if ($definition->entityScope === BusinessRecordDefinition::ENTITY_SCOPE_DEPARTMENT) {
            $columns[] = 'department_id';
        }

        // Subtype discriminators baked into the view's WHERE clause (e.g. Bill's
        // type/payable) — every column record_predicates keys off must also be
        // selectable so RAG's executor can see which predicate a row satisfies.
        foreach (array_keys($definition->recordPredicates) as $column) {
            $columns[] = $column;
        }

        // The parent FK column itself (e.g. credit_notes.bill_id), alongside the
        // parent's entity_id already added above.
        if ($definition->parentRelation !== null) {
            $columns[] = "{$definition->parentRelation}_id";
        }

        return $columns;
    }
}
