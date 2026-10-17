<?php

declare(strict_types=1);

namespace App\Authorization\BusinessQuery;

/**
 * Resource → ai_v1_bq_* view binding with scope + subtype predicates.
 *
 * The record-link group declares how one record resolves for a viewer: the
 * Laravel route name that opens the record, the permission(s) that grant the
 * link, and the chip word the panel shows. A binding without a recordRoute is
 * not resolvable — the RAG bundle treats it as plain text.
 */
readonly class ResourceBinding
{
    /**
     * @param  array{entity: ?string, department: ?string}  $scopeColumns
     * @param  array<string, string>  $recordPredicates
     * @param  list<string>  $linkPermissions  Permission(s) required to render a navigable link; empty means the resource has no link grant
     */
    public function __construct(
        public string $name,
        public string $projectionView,
        public array $scopeColumns,
        public ?string $departmentScopeMode,
        public string $primaryKey,
        public array $recordPredicates,
        public string $description = '',
        /** Canonical public identifier used by named-record questions. */
        public ?string $referenceDimension = null,
        /** Laravel route name that opens one record of this resource. */
        public ?string $recordRoute = null,
        /** Route parameter name the record id fills. */
        public ?string $recordRouteParam = null,
        public array $linkPermissions = [],
        /** 'any' (a single grant suffices) or 'all' (every grant required). */
        public string $linkPermissionMode = 'any',
        /** Human chip word shown on links and record references, e.g. 'Invoice'. */
        public ?string $label = null,
    ) {}
}
