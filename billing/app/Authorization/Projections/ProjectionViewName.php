<?php

declare(strict_types=1);

namespace App\Authorization\Projections;

/**
 * Single source of truth for the `ai_v1_*` view-naming convention (Task B5 /
 * R1). `v1` is the version: a future shape change mints `ai_v2_<resource>`
 * and leaves this one in place — never edit a shipped view's SELECT list.
 *
 * PolicyManifestExporter's `projection_name` manifest field and
 * ProjectionDefinitions' view names both resolve through this one function
 * so they can never drift apart (ProjectionDefinitionsTest asserts the two
 * stay equal).
 */
final class ProjectionViewName
{
    private function __construct() {}

    public static function forResource(string $resourceType): string
    {
        return "ai_v1_{$resourceType}";
    }
}
