<?php

namespace App\Models;

use App\Helpers\BadgeHelper;
use App\Models\Concerns\LogsModelActivity;
use App\Traits\EntityTrait;
use IFRS\Models\Entity;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\Relations\HasMany;

class Inventory extends Model
{
    use EntityTrait;
    use HasFactory;
    use LogsModelActivity;

    public function entity()
    {
        return $this->belongsTo(Entity::class);
    }

    protected $guarded = false;

    protected $casts = [
        'charges_in_tzs' => 'boolean',
        'is_goods_costing' => 'boolean',
        'exchange_rate' => 'decimal:4',
        'inventory_date' => 'date',
    ];

    public function supplier()
    {
        return $this->belongsTo(Supplier::class);
    }

    /**
     * Currency for foreign currency invoices.
     */
    public function currency()
    {
        return $this->belongsTo(Currency::class);
    }

    /**
     * Supplier bill linked to this receive entry (when invoice arrives).
     */
    public function supplierBill()
    {
        return $this->belongsTo(Inventory::class, 'supplier_bill_id');
    }

    /**
     * Receive entries linked to this supplier bill.
     */
    public function linkedReceiveEntries()
    {
        return $this->hasMany(Inventory::class, 'supplier_bill_id');
    }

    public function from()
    {
        return $this->belongsTo(WareHouse::class, 'transfer_out');
    }

    public function to()
    {
        return $this->belongsTo(WareHouse::class, 'transfer_in');
    }

    public function inventoryItems(): HasMany
    {
        return $this->hasMany(InventoryItem::class);
    }

    public function workOrder()
    {
        return $this->belongsTo(WorkOrder::class);
    }

    public function warehouse()
    {
        return $this->belongsTo(WareHouse::class, 'ware_house_id');
    }

    /**
     * Get the user who created this inventory entry.
     */
    public function createdBy(): BelongsTo
    {
        return $this->belongsTo(User::class, 'created_by');
    }

    public function payments()
    {
        return $this->hasMany(Journal::class, 'inventory_id')->whereHas('account', function ($account) {
            $account->whereIn('account_type',
                [Account::CASH, Account::BANK]);
        });
    }

    public function getBalanceAttribute()
    {
        return $this->balance();
    }

    public function balance()
    {
        $cleared_amount = 0;

        foreach ($this->payments as $entry) {
            $cleared_amount = $cleared_amount + $entry->credit;
        }

        return $this->amount() - $cleared_amount;
    }

    public function amount()
    {
        // For goods costing bills, use the stored total landed cost
        // (items belong to linked receive entries, not this bill directly)
        if ($this->is_goods_costing && $this->total_landed_cost_tzs > 0) {
            return $this->total_landed_cost_tzs;
        }

        $value = 0;
        foreach ($this->inventoryItems as $item) {
            $value = $value + ($item->cost * $item->quantity);
        }

        return $value;
    }

    public function balanceStatus()
    {
        $balance = $this->balance();
        if ($balance == 0) {
            return BadgeHelper::render('PAID', 'success');
        } elseif ($balance > 0 && $balance != $this->amount()) {
            return BadgeHelper::render('PARTIAL PAID', 'sky');
        } else {
            return BadgeHelper::render('UNPAID', 'danger');
        }
    }

    /**
     * Calculate total charges (clearing + freight + other).
     */
    public function totalCharges(): float
    {
        return ($this->clearing_charges ?? 0) +
               ($this->freight_charges ?? 0) +
               ($this->other_charges ?? 0);
    }

    /**
     * Calculate total charges in USD.
     */
    public function totalChargesTzs(): float
    {
        if ($this->charges_in_tzs) {
            return $this->totalCharges();
        }

        return $this->totalCharges() * ($this->exchange_rate ?? 1);
    }

    /**
     * Check if this receive entry has costing information.
     */
    public function hasCosting(): bool
    {
        return $this->type === 'Receive' &&
               ($this->currency_id !== null || $this->totalCharges() > 0);
    }

    /**
     * Check if this is an international (foreign currency) shipment.
     */
    public function isInternational(): bool
    {
        return $this->currency_id !== null && ($this->exchange_rate ?? 1) != 1;
    }

    /**
     * Get the allocation ratio for distributing charges across items.
     */
    public function allocationRatio(): float
    {
        $totalInvoice = $this->invoice_value_foreign ?? 0;
        if ($totalInvoice <= 0) {
            return 1;
        }

        $totalCharges = $this->totalCharges();
        if ($this->charges_in_tzs && ($this->exchange_rate ?? 1) > 0) {
            $totalCharges = $totalCharges / $this->exchange_rate;
        }

        return ($totalInvoice + $totalCharges) / $totalInvoice;
    }

    /**
     * Check if a supplier bill is attached.
     */
    public function hasBillAttached(): bool
    {
        return $this->supplier_bill_id !== null;
    }

    protected static function booted()
    {
        static::creating(function ($model) {
            static::assignEntityIdIfMissing($model);
            $model->created_by = auth()->id();
            // Auto-approve all inventory entries (approval workflow temporarily disabled)
            $model->status = 'APPROVED';
        });
    }
}
