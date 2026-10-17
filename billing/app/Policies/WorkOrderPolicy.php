<?php

declare(strict_types=1);

namespace App\Policies;

use App\Models\User;
use App\Models\WorkOrder;

final class WorkOrderPolicy
{
    public function viewAny(User $user): bool
    {
        return $user->can('view job');
    }

    public function view(User $user, WorkOrder $workOrder): bool
    {
        return $user->can('view job') && $this->visible($user, $workOrder);
    }

    public function create(User $user): bool
    {
        return $user->can('add job');
    }

    public function update(User $user, WorkOrder $workOrder): bool
    {
        return $user->can('edit job') && $this->visible($user, $workOrder);
    }

    public function delete(User $user, WorkOrder $workOrder): bool
    {
        return $user->can('delete job') && $this->visible($user, $workOrder);
    }

    public function move(User $user, WorkOrder $workOrder): bool
    {
        return $user->can('move job') && $this->visible($user, $workOrder);
    }

    public function copy(User $user, WorkOrder $workOrder): bool
    {
        return $user->can('add job') && $this->visible($user, $workOrder);
    }

    public function visible(User $user, WorkOrder $workOrder): bool
    {
        if (! $user->hasRole('Super Admin')
            && (int) $workOrder->entity_id !== (int) $user->entity_id) {
            return false;
        }

        if ($user->department_id !== null
            && (int) $workOrder->department_id !== (int) $user->department_id) {
            return false;
        }

        return true;
    }
}
