<?php

namespace App\Models;

use App\Authorization\AiAccessPolicy;
use App\Helpers\BadgeHelper;
use App\Models\Concerns\LogsModelActivity;
use App\Support\BillPaymentState;
use App\Traits\EntityTrait;
use Illuminate\Database\Eloquent\Builder;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\Relations\HasMany;

class Bill extends Model
{
    use EntityTrait;
    use HasFactory;
    use LogsModelActivity;

    protected $guarded = false;

    public function entity()
    {
        return $this->belongsTo(Entity::class);
    }

    public function warehouse()
    {
        return $this->belongsTo(WareHouse::class);
    }

    public function bank()
    {
        return $this->belongsTo(Account::class, 'preferred_method');
    }

    public function customer(): BelongsTo
    {
        return $this->belongsTo(Customer::class);
    }

    public function currency()
    {
        return $this->belongsTo(Currency::class);
    }

    public function billItems(): HasMany
    {
        return $this->hasMany(BillItem::class);
    }

    public function creditNote()
    {
        return $this->hasOne(CreditNote::class);
    }

    public function amount()
    {
        return BillPaymentState::itemsTotal($this->billItems);
    }

    public function getBalanceAttribute()
    {
        return $this->balance();
    }

    public function balance()
    {
        return $this->amount() - BillPaymentState::paymentsTotal($this->payments);
    }

    public function payments()
    {
        return $this->hasMany(Journal::class)
            ->whereHas('account', function ($account) {
                $account->whereIn('account_type',
                    [Account::OPERATING_EXPENSE, Account::PAYABLE, Account::CASH, Account::BANK]);
            })
            ->where('debit', '>', 0);  // Filter on Journal.debit, not Account
    }

    public function controlNumber(): BelongsTo
    {
        return $this->belongsTo(ControlNumber::class);
    }

    public function customerOrder(): BelongsTo
    {
        return $this->belongsTo(CustomerOrder::class);
    }

    public function workOrders()
    {
        return $this->hasMany(WorkOrder::class);
    }

    /**
     * Single source of truth for the bill's payment state.
     *
     * Delegates to the pure App\Support\BillPaymentState (see that class for
     * characterization tests) — this method stays as the stable public API.
     */
    public function paymentState(): string
    {
        return BillPaymentState::compute($this->billItems, $this->payments)->status;
    }

    /** Render the standard payment-status pill for this bill. */
    public function paymentBadge(): string
    {
        $state = $this->paymentState();

        $color = match ($state) {
            'PAID' => 'success',
            'OVERPAID' => 'warning',
            'PARTIAL PAID' => 'sky',
            default => 'danger', // UNPAID
        };

        return BadgeHelper::render($state, $color);
    }

    /** @deprecated Thin alias kept for existing view callsites — use paymentBadge(). */
    public function balanceStatus()
    {
        return $this->paymentBadge();
    }

    public function needPayment(): bool
    {
        if ($this->workOrders) {
            foreach ($this->workOrders as $workOrder) {
                if ($this->balance() > 0 && ($workOrder->status == 'FINISHED' ||
                        $workOrder->status == 'DELIVERED')) {
                    return true;
                }

                return false;
            }

        }

        return false;
    }

    public function transactions()
    {
        return $this->hasMany(Journal::class);
    }

    /**
     * Task B2: this scope is now registered UNCONDITIONALLY (unlike the old
     * role-keyed version), so `boot()` itself makes no decision and captures
     * no ambient state. The closure below is what runs LAZILY, once per
     * query, and it is the closure — not boot() — that resolves "who is
     * asking right now" and decides whether to filter. This is what makes
     * boot order irrelevant: it no longer matters who was authenticated (or
     * whether anyone was) the first time this model class was booted in a
     * given process/worker.
     */
    protected static function boot()
    {
        parent::boot();

        static::addGlobalScope('payable', function (Builder $builder) {
            if (! static::currentUserCanViewPayableQuotations()) {
                $builder->where('payable', 'No');
            }
        });
    }

    /**
     * The explicit-permission visibility rule for payable quotations (Task
     * B2), delegated to the pure App\Authorization\AiAccessPolicy so this
     * scope and BillPolicy/GlobalSearchService stay in agreement by
     * construction. Only this method touches ambient state (auth()) — it
     * resolves the CURRENT user and the current, explicit, role-aware
     * permission list, then hands both to the pure policy, which never
     * reads auth() itself.
     *
     * Fail-closed: no authenticated user (unauthenticated request or a CLI
     * context) means "cannot view payable quotations" — same default as
     * before this change.
     */
    private static function currentUserCanViewPayableQuotations(): bool
    {
        $user = auth()->user();

        if (! $user) {
            return false;
        }

        $policy = AiAccessPolicy::forUser($user, $user->getAllPermissions()->pluck('name')->all());

        return $policy->capabilities('payable_quotation')['read'];
    }
}
