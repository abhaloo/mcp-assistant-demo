<?php

namespace App\Models;

use App\Models\Concerns\LogsModelActivity;
use App\Traits\EntityTrait;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;

class Account extends Model
{
    use EntityTrait;
    use HasFactory;
    use LogsModelActivity;

    protected $guarded = false;

    /*
     * Account Type
     */
    const NON_CURRENT_ASSET = 'NON_CURRENT_ASSET';

    const CONTRA_ASSET = 'CONTRA_ASSET';

    const INVENTORY = 'INVENTORY';

    const BANK = 'BANK';

    const CASH = 'CASH';

    const CURRENT_ASSET = 'CURRENT_ASSET';

    const RECEIVABLE = 'RECEIVABLE';

    const NON_CURRENT_LIABILITY = 'NON_CURRENT_LIABILITY';

    const CONTROL = 'CONTROL';

    const CURRENT_LIABILITY = 'CURRENT_LIABILITY';

    const PAYABLE = 'PAYABLE';

    const EQUITY = 'EQUITY';

    const OPERATING_REVENUE = 'OPERATING_REVENUE';

    const OPERATING_EXPENSE = 'OPERATING_EXPENSE';

    const NON_OPERATING_REVENUE = 'NON_OPERATING_REVENUE';

    const DIRECT_EXPENSE = 'DIRECT_EXPENSE';

    const OVERHEAD_EXPENSE = 'OVERHEAD_EXPENSE';

    const OTHER_EXPENSE = 'OTHER_EXPENSE';

    const RECONCILIATION = 'RECONCILIATION';

    /*
     *  Account that are purchasable
     */
    const PURCHASABLES = [
        Account::OPERATING_EXPENSE,
        Account::DIRECT_EXPENSE,
        Account::OVERHEAD_EXPENSE,
        Account::OTHER_EXPENSE,
        Account::NON_CURRENT_ASSET,
        Account::CURRENT_ASSET,
        Account::INVENTORY,
    ];

    public function currency()
    {
        return $this->belongsTo(Currency::class);
    }

    public function balance($start_date = null, $end_date = null, $customer_id = null, $supplier_id = null)
    {
        $transactions = Journal::query()->where('account_id', $this->id);

        // Determine date range based on provided parameters
        // Uses whereDate for database-agnostic date comparison
        if (! $start_date && ! $end_date) {
            // No dates: year-to-date (current year start to today)
            $transactions->whereDate('post_date', '>=', date('Y-01-01'))
                ->whereDate('post_date', '<=', date('Y-m-d'));
        } elseif (! $start_date && $end_date) {
            // Only end_date: from inception to end_date (for balance sheet)
            $transactions->whereDate('post_date', '<=', $end_date);
        } elseif ($start_date && ! $end_date) {
            // Only start_date: from start_date to today
            $transactions->whereDate('post_date', '>=', $start_date)
                ->whereDate('post_date', '<=', date('Y-m-d'));
        } else {
            // Both dates provided: specific date range
            $transactions->whereDate('post_date', '>=', $start_date)
                ->whereDate('post_date', '<=', $end_date);
        }

        if ($customer_id) {
            $transactions->where('customer_id', $customer_id);
        }

        if ($supplier_id) {
            $transactions->where('supplier_id', $supplier_id);
        }

        $transactions = $transactions->get();

        $total_debit = 0;
        $total_credit = 0;
        foreach ($transactions as $tx) {
            $total_debit = $total_debit + $tx->debit;
            $total_credit = $total_credit + $tx->credit;
        }

        if (in_array($this->account_type, config('accounting.asset_accounts')) ||
            in_array($this->account_type, config('accounting.operating_expenses_accounts')) ||
            in_array($this->account_type, config('accounting.non_operating_expenses_accounts'))) {
            $balance = $total_debit - $total_credit;
        } else {
            $balance = $total_credit - $total_debit;
        }

        return $balance;
    }

    public function totalEntries($start_date, $end_date)
    {

        $transactions = Journal::where('account_id', $this->id)
            ->whereBetween('post_date', [$start_date, $end_date])->get();

        $total_debit = 0;
        $total_credit = 0;
        foreach ($transactions as $tx) {
            if ($tx->credit == 0) {
                $total_debit = $total_debit + $tx->debit;
            } else {
                $total_credit = $total_credit + $tx->credit;
            }
        }

        return [
            'total_debit' => $total_debit,
            'total_credit' => $total_credit,
        ];
    }
}
