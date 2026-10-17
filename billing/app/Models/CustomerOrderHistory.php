<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

class CustomerOrderHistory extends Model
{
    use HasFactory;

    protected $guarded = false;

    protected $casts = [
        'metadata' => 'array',
    ];

    // Event type constants
    public const ORDER_CREATED = 'ORDER_CREATED';

    public const ORDER_UPDATED = 'ORDER_UPDATED';

    public const ORDER_CANCELLED = 'ORDER_CANCELLED';

    public const ORDER_FINISHED = 'ORDER_FINISHED';

    public const ORDER_REOPENED = 'ORDER_REOPENED';

    public const JOB_CREATED = 'JOB_CREATED';

    public const JOB_ATTACHED = 'JOB_ATTACHED';

    public const JOB_DETACHED = 'JOB_DETACHED';

    public const JOB_STATUS_CHANGED = 'JOB_STATUS_CHANGED';

    public const QUOTATION_CREATED = 'QUOTATION_CREATED';

    public const QUOTATION_ATTACHED = 'QUOTATION_ATTACHED';

    public const INVOICE_CREATED = 'INVOICE_CREATED';

    public const INVOICE_CONVERTED = 'INVOICE_CONVERTED';

    public const BILL_DETACHED = 'BILL_DETACHED';

    public const QUOTATION_UPDATED = 'QUOTATION_UPDATED';

    public const INVOICE_UPDATED = 'INVOICE_UPDATED';

    public const PAYABLE_QUOTATION_CONVERTED = 'PAYABLE_QUOTATION_CONVERTED';

    public const JOB_DEPARTMENT_CHANGED = 'JOB_DEPARTMENT_CHANGED';

    public const JOB_PRIORITY_CHANGED = 'JOB_PRIORITY_CHANGED';

    public function customerOrder(): BelongsTo
    {
        return $this->belongsTo(CustomerOrder::class);
    }

    public function user(): BelongsTo
    {
        return $this->belongsTo(User::class);
    }

    /**
     * Get an icon for the event type.
     */
    public function getIconAttribute(): string
    {
        return match ($this->event_type) {
            self::ORDER_CREATED => '<i class="fa fa-plus-circle text-success"></i>',
            self::ORDER_UPDATED => '<i class="fa fa-edit text-info"></i>',
            self::ORDER_CANCELLED => '<i class="fa fa-ban text-danger"></i>',
            self::ORDER_FINISHED => '<i class="fa fa-check-circle text-success"></i>',
            self::ORDER_REOPENED => '<i class="fa fa-redo text-warning"></i>',
            self::JOB_CREATED => '<i class="fa fa-briefcase text-primary"></i>',
            self::JOB_ATTACHED => '<i class="fa fa-link text-info"></i>',
            self::JOB_DETACHED => '<i class="fa fa-unlink text-warning"></i>',
            self::JOB_STATUS_CHANGED => '<i class="fa fa-exchange-alt text-secondary"></i>',
            self::JOB_DEPARTMENT_CHANGED => '<i class="fa fa-arrows-alt text-purple"></i>',
            self::JOB_PRIORITY_CHANGED => '<i class="fa fa-exclamation-triangle text-warning"></i>',
            self::QUOTATION_CREATED, self::QUOTATION_ATTACHED => '<i class="fa fa-file-alt text-info"></i>',
            self::QUOTATION_UPDATED => '<i class="fa fa-edit text-info"></i>',
            self::INVOICE_CREATED, self::INVOICE_CONVERTED => '<i class="fa fa-file-invoice text-success"></i>',
            self::INVOICE_UPDATED => '<i class="fa fa-edit text-success"></i>',
            self::PAYABLE_QUOTATION_CONVERTED => '<i class="fa fa-file-invoice-dollar text-primary"></i>',
            self::BILL_DETACHED => '<i class="fa fa-unlink text-warning"></i>',
            default => '<i class="fa fa-circle text-muted"></i>',
        };
    }
}
