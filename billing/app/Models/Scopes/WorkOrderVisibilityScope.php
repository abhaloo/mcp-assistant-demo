<?php

declare(strict_types=1);

namespace App\Models\Scopes;

use App\Models\User;
use App\Models\WorkOrder;
use Illuminate\Database\Eloquent\Builder;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Scope;

/**
 * Centralized WorkOrder visibility: entity isolation + optional department rule.
 *
 * Within an allowed entity, users with no department see all jobs; department-assigned
 * users see only jobs in that department. Super Admin bypasses entity isolation.
 */
final class WorkOrderVisibilityScope implements Scope
{
    public function apply(Builder $builder, Model $model): void
    {
        $user = auth()->user();
        if (! $user instanceof User) {
            return;
        }

        self::applyToBuilder($builder, $user, $model->getTable(), true);
    }

    public static function applyToBuilder(
        Builder $builder,
        User $user,
        ?string $table = null,
        bool $includeDepartment = true,
    ): void {
        $table = $table ?? (new WorkOrder)->getTable();

        if (! $user->hasRole('Super Admin')) {
            $builder->where("{$table}.entity_id", $user->entity_id);
        }

        if ($includeDepartment && $user->department_id !== null) {
            $builder->where("{$table}.department_id", (int) $user->department_id);
        }
    }
}
