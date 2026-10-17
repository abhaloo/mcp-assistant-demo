<?php

declare(strict_types=1);

namespace App\Policies;

use App\Models\Customer;
use App\Models\User;

final class CustomerPolicy
{
    public function view(User $user, Customer $customer): bool
    {
        if (! $user->can('view customer')) {
            return false;
        }

        if ($user->hasRole('Super Admin')) {
            return true;
        }

        return (int) $customer->entity_id === (int) $user->entity_id;
    }
}
