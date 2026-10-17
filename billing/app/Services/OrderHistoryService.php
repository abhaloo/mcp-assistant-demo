<?php

namespace App\Services;

use App\Models\Bill;
use App\Models\CustomerOrder;
use App\Models\CustomerOrderHistory;
use App\Models\WorkOrder;

class OrderHistoryService
{
    /**
     * Log an event to the order history.
     */
    public static function log(
        CustomerOrder $order,
        string $eventType,
        string $description,
        ?array $metadata = null
    ): CustomerOrderHistory {
        return CustomerOrderHistory::create([
            'customer_order_id' => $order->id,
            'user_id' => auth()->id(),
            'event_type' => $eventType,
            'description' => $description,
            'metadata' => $metadata,
        ]);
    }

    public static function logOrderCreated(CustomerOrder $order): void
    {
        self::log(
            $order,
            CustomerOrderHistory::ORDER_CREATED,
            "Order #{$order->order_number} created",
            ['order_id' => $order->id]
        );
    }

    public static function logOrderUpdated(CustomerOrder $order, array $changes): void
    {
        $changedFields = implode(', ', array_keys($changes));
        self::log(
            $order,
            CustomerOrderHistory::ORDER_UPDATED,
            "Order updated: {$changedFields}",
            ['changes' => $changes]
        );
    }

    public static function logOrderCancelled(CustomerOrder $order, string $reason): void
    {
        self::log(
            $order,
            CustomerOrderHistory::ORDER_CANCELLED,
            "Order cancelled: {$reason}",
            ['reason' => $reason]
        );
    }

    public static function logOrderFinished(CustomerOrder $order): void
    {
        self::log(
            $order,
            CustomerOrderHistory::ORDER_FINISHED,
            "Order #{$order->order_number} marked as finished"
        );
    }

    public static function logOrderReopened(CustomerOrder $order, string $previousStatus): void
    {
        self::log(
            $order,
            CustomerOrderHistory::ORDER_REOPENED,
            "Order #{$order->order_number} re-opened (was {$previousStatus})",
            ['previous_status' => $previousStatus]
        );
    }

    public static function logJobCreated(CustomerOrder $order, WorkOrder $job): void
    {
        $jobNumber = $job->work_number ?? $job->id;
        self::log(
            $order,
            CustomerOrderHistory::JOB_CREATED,
            "Job #{$jobNumber} \"{$job->title}\" created",
            ['work_order_id' => $job->id, 'work_number' => $jobNumber]
        );
    }

    public static function logJobAttached(CustomerOrder $order, WorkOrder $job): void
    {
        $jobNumber = $job->work_number ?? $job->id;
        self::log(
            $order,
            CustomerOrderHistory::JOB_ATTACHED,
            "Job #{$jobNumber} \"{$job->title}\" attached",
            ['work_order_id' => $job->id, 'work_number' => $jobNumber]
        );
    }

    public static function logJobDetached(CustomerOrder $order, WorkOrder $job): void
    {
        $jobNumber = $job->work_number ?? $job->id;
        self::log(
            $order,
            CustomerOrderHistory::JOB_DETACHED,
            "Job #{$jobNumber} \"{$job->title}\" detached",
            ['work_order_id' => $job->id, 'work_number' => $jobNumber]
        );
    }

    public static function logJobStatusChanged(WorkOrder $job, string $oldStatus, string $newStatus): void
    {
        if (! $job->customerOrder) {
            return;
        }

        $jobNumber = $job->work_number ?? $job->id;
        self::log(
            $job->customerOrder,
            CustomerOrderHistory::JOB_STATUS_CHANGED,
            "Job #{$jobNumber} status: {$oldStatus} → {$newStatus}",
            [
                'work_order_id' => $job->id,
                'work_number' => $jobNumber,
                'old_status' => $oldStatus,
                'new_status' => $newStatus,
            ]
        );
    }

    public static function logQuotationCreated(CustomerOrder $order, Bill $bill): void
    {
        $billNumber = $bill->invoice_number ?? $bill->id;
        self::log(
            $order,
            CustomerOrderHistory::QUOTATION_CREATED,
            "Quotation #{$billNumber} created",
            ['bill_id' => $bill->id, 'invoice_number' => $bill->invoice_number]
        );
    }

    public static function logQuotationAttached(CustomerOrder $order, Bill $bill): void
    {
        $billNumber = $bill->invoice_number ?? $bill->id;
        self::log(
            $order,
            CustomerOrderHistory::QUOTATION_ATTACHED,
            "Quotation #{$billNumber} attached",
            ['bill_id' => $bill->id, 'invoice_number' => $bill->invoice_number]
        );
    }

    public static function logInvoiceCreated(CustomerOrder $order, Bill $bill): void
    {
        $billNumber = $bill->invoice_number ?? $bill->id;
        self::log(
            $order,
            CustomerOrderHistory::INVOICE_CREATED,
            "Invoice #{$billNumber} created",
            ['bill_id' => $bill->id, 'invoice_number' => $bill->invoice_number]
        );
    }

    public static function logInvoiceConverted(CustomerOrder $order, Bill $bill): void
    {
        $billNumber = $bill->invoice_number ?? $bill->id;
        self::log(
            $order,
            CustomerOrderHistory::INVOICE_CONVERTED,
            "Quotation converted to Invoice #{$billNumber}",
            ['bill_id' => $bill->id, 'invoice_number' => $bill->invoice_number]
        );
    }

    public static function logBillDetached(CustomerOrder $order, Bill $bill): void
    {
        $billNumber = $bill->invoice_number ?? $bill->id;
        self::log(
            $order,
            CustomerOrderHistory::BILL_DETACHED,
            "{$bill->type} #{$billNumber} detached",
            ['bill_id' => $bill->id, 'invoice_number' => $bill->invoice_number, 'type' => $bill->type]
        );
    }

    public static function logQuotationUpdated(CustomerOrder $order, Bill $bill): void
    {
        $billNumber = $bill->invoice_number ?? $bill->id;
        self::log(
            $order,
            CustomerOrderHistory::QUOTATION_UPDATED,
            "Quotation #{$billNumber} updated",
            ['bill_id' => $bill->id, 'invoice_number' => $bill->invoice_number]
        );
    }

    public static function logInvoiceUpdated(CustomerOrder $order, Bill $bill): void
    {
        $billNumber = $bill->invoice_number ?? $bill->id;
        self::log(
            $order,
            CustomerOrderHistory::INVOICE_UPDATED,
            "Invoice #{$billNumber} updated",
            ['bill_id' => $bill->id, 'invoice_number' => $bill->invoice_number]
        );
    }

    public static function logPayableQuotationConverted(CustomerOrder $order, Bill $bill): void
    {
        $billNumber = $bill->invoice_number ?? $bill->id;
        self::log(
            $order,
            CustomerOrderHistory::PAYABLE_QUOTATION_CONVERTED,
            "Quotation converted to Payable Quotation #{$billNumber}",
            ['bill_id' => $bill->id, 'invoice_number' => $bill->invoice_number]
        );
    }

    public static function logJobDepartmentChanged(WorkOrder $job, string $oldDepartment, string $newDepartment): void
    {
        if (! $job->customerOrder) {
            return;
        }

        $jobNumber = $job->work_number ?? $job->id;
        self::log(
            $job->customerOrder,
            CustomerOrderHistory::JOB_DEPARTMENT_CHANGED,
            "Job #{$jobNumber} moved: {$oldDepartment} → {$newDepartment}",
            [
                'work_order_id' => $job->id,
                'work_number' => $jobNumber,
                'old_department' => $oldDepartment,
                'new_department' => $newDepartment,
            ]
        );
    }

    public static function logJobPriorityChanged(WorkOrder $job, string $oldPriority, string $newPriority): void
    {
        if (! $job->customerOrder) {
            return;
        }

        $jobNumber = $job->work_number ?? $job->id;
        self::log(
            $job->customerOrder,
            CustomerOrderHistory::JOB_PRIORITY_CHANGED,
            "Job #{$jobNumber} priority changed: ".ucfirst($oldPriority).' → '.ucfirst($newPriority),
            [
                'work_order_id' => $job->id,
                'work_number' => $jobNumber,
                'old_priority' => $oldPriority,
                'new_priority' => $newPriority,
            ]
        );
    }
}
