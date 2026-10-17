<?php

declare(strict_types=1);

namespace App\Policies;

use App\Models\CustomerOrder;
use App\Models\User;

final class CustomerOrderPolicy
{
    public function view(User $user, CustomerOrder $customerOrder): bool
    {
        if (! $user->can('view customer order')) {
            return false;
        }

        if ($user->hasRole('Super Admin')) {
            return true;
        }

        return (int) $customerOrder->entity_id === (int) $user->entity_id;
    }
}
