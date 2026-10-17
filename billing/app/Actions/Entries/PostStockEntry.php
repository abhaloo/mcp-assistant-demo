<?php

namespace App\Actions\Entries;

use App\Models\Account;
use App\Models\Journal;
use App\Models\Period;
use Illuminate\Support\Facades\DB;

class PostStockEntry
{
    /**
     * Handle stock entry posting
     */
    public static function handle(
        float $amount,
        int $debit_account_id,
        int $credit_account_id,
        string $description,
        string $post_date,
        int $period_id,
        int $currency_id = 1,
        ?string $reference = null,
        ?string $document_no = null,
        string $type = 'Closing Stock'
    ): array {

        DB::beginTransaction();

        try {
            $entity_id = auth()->user()->entity_id;
            $period = Period::findOrFail($period_id);

            // Generate document number if not provided
            if (! $document_no) {
                $document_no = self::generateDocumentNumber($entity_id, $period, $type);
            }

            // Validate accounts exist
            $debit_account = Account::findOrFail($debit_account_id);
            $credit_account = Account::findOrFail($credit_account_id);

            // Ensure accounts belong to the same entity
            if ($debit_account->entity_id !== $entity_id || $credit_account->entity_id !== $entity_id) {
                throw new \Exception('Accounts must belong to the same entity');
            }

            $exchange_rate = 1.0000; // Default exchange rate
            $foreign_amount = $amount; // Assuming same currency for now

            // Create debit entry
            $debit_entry = Journal::create([
                'cash' => false,
                'type' => $type,
                'entity_id' => $entity_id,
                'period_id' => $period_id,
                'currency_id' => $currency_id,
                'document_no' => $document_no,
                'post_date' => $post_date,
                'account_id' => $debit_account_id,
                'bill_id' => null,
                'inventory_id' => null,
                'customer_id' => null,
                'supplier_id' => null,
                'reference' => $reference,
                'receipt_number' => null,
                'voucher_number' => null,
                'item_id' => null,
                'debit' => $amount,
                'credit' => 0.00,
                'foreign_debit' => $foreign_amount,
                'foreign_credit' => 0.00,
                'exchange_rate' => $exchange_rate,
                'description' => $description,
                'created_at' => now(),
                'updated_at' => now(),
            ]);

            // Create credit entry
            $credit_entry = Journal::create([
                'cash' => false,
                'type' => $type,
                'entity_id' => $entity_id,
                'period_id' => $period_id,
                'currency_id' => $currency_id,
                'document_no' => $document_no,
                'post_date' => $post_date,
                'account_id' => $credit_account_id,
                'bill_id' => null,
                'inventory_id' => null,
                'customer_id' => null,
                'supplier_id' => null,
                'reference' => $reference,
                'receipt_number' => null,
                'voucher_number' => null,
                'item_id' => null,
                'debit' => 0.00,
                'credit' => $amount,
                'foreign_debit' => 0.00,
                'foreign_credit' => $foreign_amount,
                'exchange_rate' => $exchange_rate,
                'description' => $description,
                'created_at' => now(),
                'updated_at' => now(),
            ]);

            DB::commit();

            return [
                'success' => true,
                'document_no' => $document_no,
                'debit_entry_id' => $debit_entry->id,
                'credit_entry_id' => $credit_entry->id,
                'message' => 'Stock entry posted successfully',
            ];

        } catch (\Exception $e) {
            DB::rollback();

            return [
                'success' => false,
                'error' => $e->getMessage(),
                'message' => 'Failed to post stock entry',
            ];
        }
    }

    /**
     * Generate document number for stock entries
     */
    private static function generateDocumentNumber(int $entity_id, Period $period, string $type = 'Closing Stock'): string
    {
        $prefix = match ($type) {
            'Closing Stock' => 'CS',
            'Stock Adjustment' => 'SA',
            'Opening Stock' => 'OS',
            default => 'ST'
        };

        $year = substr($period->calendar_year, -2);
        $period_num = str_pad($period->period_count, 2, '0', STR_PAD_LEFT);

        // Get next sequence number for this period and type
        $last_entry = Journal::where('entity_id', $entity_id)
            ->where('type', $type)
            ->where('document_no', 'LIKE', "{$prefix}{$year}{$period_num}%")
            ->orderBy('document_no', 'desc')
            ->first();

        $sequence = 1;
        if ($last_entry) {
            $last_sequence = intval(substr($last_entry->document_no, -3));
            $sequence = $last_sequence + 1;
        }

        return $prefix.$year.$period_num.str_pad($sequence, 3, '0', STR_PAD_LEFT);
    }

    /**
     * Reverse a stock entry
     */
    public static function reverse(string $document_no, int $entity_id, string $reason = 'Stock entry reversal'): array
    {
        DB::beginTransaction();

        try {
            // Get original entries
            $original_entries = Journal::where('document_no', $document_no)
                ->where('entity_id', $entity_id)
                ->get();

            if ($original_entries->isEmpty()) {
                throw new \Exception('Original stock entry not found');
            }

            $period = Period::find($original_entries->first()->period_id);
            $reversal_doc_no = self::generateDocumentNumber($entity_id, $period, 'Stock Reversal');

            // Create reversal entries (swap debit and credit)
            foreach ($original_entries as $entry) {
                Journal::create([
                    'cash' => $entry->cash,
                    'type' => 'Stock Reversal',
                    'entity_id' => $entry->entity_id,
                    'period_id' => $entry->period_id,
                    'currency_id' => $entry->currency_id,
                    'document_no' => $reversal_doc_no,
                    'post_date' => now()->format('Y-m-d'),
                    'account_id' => $entry->account_id,
                    'bill_id' => $entry->bill_id,
                    'inventory_id' => $entry->inventory_id,
                    'customer_id' => $entry->customer_id,
                    'supplier_id' => $entry->supplier_id,
                    'reference' => "REV-{$entry->reference}",
                    'receipt_number' => $entry->receipt_number,
                    'voucher_number' => $entry->voucher_number,
                    'item_id' => $entry->item_id,
                    'debit' => $entry->credit, // Swap
                    'credit' => $entry->debit, // Swap
                    'foreign_debit' => $entry->foreign_credit, // Swap
                    'foreign_credit' => $entry->foreign_debit, // Swap
                    'exchange_rate' => $entry->exchange_rate,
                    'description' => "{$reason} - Original: {$document_no}",
                    'created_at' => now(),
                    'updated_at' => now(),
                ]);
            }

            DB::commit();

            return [
                'success' => true,
                'reversal_document_no' => $reversal_doc_no,
                'message' => 'Stock entry reversed successfully',
            ];

        } catch (\Exception $e) {
            DB::rollback();

            return [
                'success' => false,
                'error' => $e->getMessage(),
                'message' => 'Failed to reverse stock entry',
            ];
        }
    }

    /**
     * Get stock entry summary for a period
     */
    public static function getStockSummary(int $entity_id, int $period_id, int $currency_id = 1): array
    {
        $inventory_account = Account::where('entity_id', $entity_id)
            ->where('account_type', Account::INVENTORY)
            ->first();

        if (! $inventory_account) {
            return [
                'opening_stock' => 0,
                'closing_stock' => 0,
                'adjustment' => 0,
                'entries_count' => 0,
            ];
        }

        $period = Period::find($period_id);

        // Get opening stock (previous period's closing)
        $opening_stock = self::getOpeningStock($entity_id, $period, $currency_id, $inventory_account);

        // Get current period adjustments
        $period_adjustments = Journal::where('entity_id', $entity_id)
            ->where('period_id', $period_id)
            ->where('currency_id', $currency_id)
            ->where('account_id', $inventory_account->id)
            ->where('type', 'Closing Stock')
            ->sum(DB::raw('debit - credit'));

        $closing_stock = $opening_stock + $period_adjustments;

        $entries_count = Journal::where('entity_id', $entity_id)
            ->where('period_id', $period_id)
            ->where('type', 'Closing Stock')
            ->count() / 2; // Divide by 2 since each entry creates 2 records

        return [
            'opening_stock' => $opening_stock,
            'closing_stock' => $closing_stock,
            'adjustment' => $period_adjustments,
            'entries_count' => $entries_count,
        ];
    }

    /**
     * Get opening stock for a period
     */
    private static function getOpeningStock(int $entity_id, Period $period, int $currency_id, Account $inventory_account): float
    {
        // For the first period, check previous year
        if ($period->period_count == 1) {
            $prev_year_period = Period::where('entity_id', $entity_id)
                ->where('calendar_year', $period->calendar_year - 1)
                ->orderBy('period_count', 'desc')
                ->first();

            if (! $prev_year_period) {
                return 0;
            }

            $period_to_check = $prev_year_period;
        } else {
            // Get previous period in same year
            $prev_period = Period::where('entity_id', $entity_id)
                ->where('calendar_year', $period->calendar_year)
                ->where('period_count', $period->period_count - 1)
                ->first();

            if (! $prev_period) {
                return 0;
            }

            $period_to_check = $prev_period;
        }

        // Calculate balance up to previous period
        $balance = Journal::where('entity_id', $entity_id)
            ->where('currency_id', $currency_id)
            ->where('account_id', $inventory_account->id)
            ->join('periods', 'journals.period_id', '=', 'periods.id')
            ->where(function ($query) use ($period_to_check) {
                $query->where('periods.calendar_year', '<', $period_to_check->calendar_year)
                    ->orWhere(function ($q) use ($period_to_check) {
                        $q->where('periods.calendar_year', $period_to_check->calendar_year)
                            ->where('periods.period_count', '<=', $period_to_check->period_count);
                    });
            })
            ->sum(DB::raw('debit - credit'));

        return $balance ?: 0;
    }
}
