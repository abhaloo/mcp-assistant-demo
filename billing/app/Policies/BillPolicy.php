<?php

declare(strict_types=1);

namespace App\Policies;

use App\Models\Bill;
use App\Models\User;

final class BillPolicy
{
    public function view(User $user, Bill $bill): bool
    {
        // Task B1 / R3: the payable-quotation entitlement correction. A bill
        // matching the canonical predicate requires the single explicit
        // 'view payable quotation' permission — 'view invoice'/'view
        // quotation' no longer substitute for it (search-only/detail-only
        // fragments are intentionally tightened; see BusinessRecordRegistry's
        // payable_quotation definition for the same predicate + permission).
        if ($bill->type === 'Payable Quotation' && $bill->payable === 'Yes') {
            return $user->can('view payable quotation') && $this->withinEntityScope($user, $bill);
        }

        if ($bill->type !== 'Invoice') {
            return false;
        }

        return $user->can('view invoice') && $this->withinEntityScope($user, $bill);
    }

    private function withinEntityScope(User $user, Bill $bill): bool
    {
        if ($user->hasRole('Super Admin')) {
            return true;
        }

        return (int) $bill->entity_id === (int) $user->entity_id;
    }
}
