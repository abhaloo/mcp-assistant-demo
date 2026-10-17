<?php

namespace App\Observers;

use App\Models\WorkOrder;
use App\Services\OrderHistoryService;

class WorkOrderObserver
{
    /**
     * Handle the WorkOrder "updating" event.
     * Log status changes for work orders linked to customer orders.
     */
    public function updating(WorkOrder $workOrder): void
    {
        if ($workOrder->isDirty('status') && $workOrder->customer_order_id) {
            OrderHistoryService::logJobStatusChanged(
                $workOrder,
                $workOrder->getOriginal('status') ?? 'CREATED',
                $workOrder->status
            );
        }
    }
}
