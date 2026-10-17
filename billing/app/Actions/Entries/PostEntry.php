<?php

namespace App\Actions\Entries;

use App\Actions\Generators\DocumentNumberGenerator;
use App\Models\Account;
use App\Models\Currency;
use App\Models\Journal;
use Illuminate\Support\Facades\DB;
use InvalidArgumentException;

class PostEntry
{
    public static function handle(
        $amount,
        $debit_account,
        $credit_account,
        $description,
        $post_date = null,
        $bill_id = null,
        $customer_id = null,
        $supplier_id = null,
        $item_id = null,
        $inventory_id = null,
        $transaction_type = 'Journal Entry',
        $reference = null,
        $receipt = null,
        $voucher_number = null,
        $cash = false
    ): string {

        if ($amount <= 0) {
            throw new InvalidArgumentException('Amount must be greater than zero');
        }

        if ($debit_account == $credit_account) {
            throw new InvalidArgumentException('Debit and credit accounts cannot be the same');
        }

        // Wrap entire posting in a transaction to ensure atomicity
        return DB::transaction(function () use (
            $amount, $debit_account, $credit_account, $description,
            $post_date, $bill_id, $customer_id, $supplier_id,
            $item_id, $inventory_id, $transaction_type, $reference,
            $receipt, $voucher_number, $cash
        ) {
            $document_no = DocumentNumberGenerator::handle();

            if (! $post_date) {
                $post_date = date('Y-m-d');
            }

            // Check currency
            $home_currency = Currency::find(1);
            $debit = Account::find($debit_account);
            $credit = Account::find($credit_account);

            // Validate accounts exist
            if (! $debit || ! $credit) {
                throw new InvalidArgumentException('Invalid debit or credit account');
            }

            $foreign_amount = 0;
            $home_amount = $amount;
            $currency = $home_currency->id;
            $exchange_rate = 1; // Default to 1 for base currency

            //            if ($debit->currency->id != $home_currency->id || $credit->currency->id != $home_currency->id) {
            //
            //                if ($debit->currency->id != $credit->currency->id) {
            //
            //                    throw new InvalidArgumentException('Debit and credit accounts must have the same currency');
            //                }
            //
            //                $foreign_amount = $amount;
            //                $home_amount = $amount * $debit->currency->rate;
            //                $currency = $debit->currency->id;
            //                $exchange_rate = $debit->currency->rate;
            //
            //            }

            // First entry (Debit side)
            Journal::create([
                'type' => $transaction_type,
                'document_no' => $document_no,
                'currency_id' => $currency,
                'period_id' => 1,
                'post_date' => $post_date,
                'account_id' => $debit_account,
                'debit' => $home_amount,
                'credit' => 0,
                'foreign_debit' => $foreign_amount,
                'foreign_credit' => 0,
                'description' => $description,
                'bill_id' => $bill_id,
                'inventory_id' => $inventory_id,
                'customer_id' => $customer_id,
                'supplier_id' => $supplier_id,
                'item_id' => $item_id,
                'exchange_rate' => $exchange_rate,
                'reference' => $reference,
                'receipt_number' => $receipt,
                'voucher_number' => $voucher_number,
                'cash' => $cash,
            ]);

            // Second entry (Credit side)
            Journal::create([
                'type' => $transaction_type,
                'document_no' => $document_no,
                'currency_id' => $currency,
                'period_id' => 1,
                'post_date' => $post_date,
                'account_id' => $credit_account,
                'credit' => $home_amount,
                'debit' => 0,
                'foreign_debit' => 0,
                'foreign_credit' => $foreign_amount,
                'description' => $description,
                'bill_id' => $bill_id,
                'inventory_id' => $inventory_id,
                'customer_id' => $customer_id,
                'supplier_id' => $supplier_id,
                'item_id' => $item_id,
                'exchange_rate' => $exchange_rate,
                'reference' => $reference,
                'receipt_number' => $receipt,
                'voucher_number' => $voucher_number,
                'cash' => $cash,
            ]);

            return $document_no;
        });
    }
}
