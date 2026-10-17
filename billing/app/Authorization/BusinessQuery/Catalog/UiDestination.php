<?php

declare(strict_types=1);

namespace App\Authorization\BusinessQuery\Catalog;

final class UiDestination
{
    /**
     * @param  list<string>  $aliases
     * @param  array<string, mixed>  $routeParams
     * @param  array<string, mixed>  $query
     * @param  list<string>  $linkPermissions
     */
    public function __construct(
        public readonly string $key,
        public readonly string $kind,
        public readonly string $label,
        public readonly array $aliases,
        public readonly string $routeName,
        public readonly array $routeParams = [],
        public readonly array $query = [],
        public readonly array $linkPermissions = [],
        public readonly string $linkPermissionMode = 'any',
    ) {}
}
