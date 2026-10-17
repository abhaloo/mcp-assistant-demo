<?php

namespace App\Models;

use App\Helpers\BadgeHelper;
use App\Models\Concerns\LogsModelActivity;
use App\Services\OrderHistoryService;
use App\Traits\EntityTrait;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\Relations\HasMany;

class CustomerOrder extends Model
{
    use EntityTrait;
    use HasFactory;
    use LogsModelActivity;

    protected $guarded = false;

    public function entity(): BelongsTo
    {
        return $this->belongsTo(Entity::class);
    }

    public function customer(): BelongsTo
    {
        return $this->belongsTo(Customer::class);
    }

    public function workOrders(): HasMany
    {
        return $this->hasMany(WorkOrder::class);
    }

    public function bills(): HasMany
    {
        return $this->hasMany(Bill::class);
    }

    /**
     * Backward-compatible accessor that returns the latest bill.
     * Allows existing code using $order->bill to continue working.
     */
    public function getBillAttribute()
    {
        return $this->bills->sortByDesc('id')->first();
    }

    /**
     * Check if the order has at least one invoice.
     */
    public function hasInvoice(): bool
    {
        return $this->bills()->where('type', 'Invoice')->exists();
    }

    /**
     * Check if the order has only quotations (no invoices).
     */
    public function hasOnlyQuotations(): bool
    {
        return $this->bills()->exists()
            && ! $this->bills()->where('type', 'Invoice')->exists();
    }

    public function histories(): HasMany
    {
        return $this->hasMany(CustomerOrderHistory::class)->with('user')->latest();
    }

    public function getStatusBadgeAttribute(): string
    {
        return match ($this->computed_status) {
            'CANCELLED' => BadgeHelper::render('CANCELLED', 'danger'),
            'FINISHED' => BadgeHelper::render('FINISHED', 'success'),
            'NEW' => BadgeHelper::render('NEW', 'secondary'),
            'IN_PROGRESS' => BadgeHelper::render('IN PROGRESS', 'info'),
            default => BadgeHelper::render('UNKNOWN', 'secondary'),
        };
    }

    public function getComputedStatusAttribute(): string
    {
        if ($this->status === 'CANCELLED') {
            return 'CANCELLED';
        }

        if ($this->status === 'FINISHED') {
            return 'FINISHED';
        }

        $totalJobs = $this->workOrders()->count();

        if ($totalJobs === 0) {
            return 'NEW';
        }

        return 'IN_PROGRESS';
    }

    public static function generateOrderNumber(): string
    {
        $lastOrder = self::orderBy('id', 'desc')->first();
        $nextNumber = $lastOrder ? ((int) $lastOrder->order_number + 1) : 1;

        return str_pad($nextNumber, 3, '0', STR_PAD_LEFT);
    }

    protected static function booted()
    {
        static::creating(function ($model) {
            static::assignEntityIdIfMissing($model);

            if (empty($model->order_number)) {
                $model->order_number = self::generateOrderNumber();
            }
        });

        static::created(function ($model) {
            OrderHistoryService::logOrderCreated($model);
        });
    }
}
