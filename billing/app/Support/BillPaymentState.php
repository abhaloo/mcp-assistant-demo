<?php

declare(strict_types=1);

namespace App\Support;

/**
 * Pure extraction of Bill::amount()/balance()/paymentState() (app/Models/Bill.php ~57-127).
 *
 * Item aggregates (bill items -> gross value including tax, minus discount) and
 * qualifying-payment aggregates (already-filtered Journal debits, see
 * Bill::payments()) are summed SEPARATELY and only combined at the end via a
 * subtraction — never joined — so a bill with N items and M qualifying
 * payments never produces an N*M cross-product of rows.
 *
 * This class takes plain iterables of value objects (not Eloquent models), so
 * it never touches the database or ambient auth state — callers pass in
 * whatever collection they already loaded (e.g. Bill::$billItems / Bill::$payments).
 */
final readonly class BillPaymentState
{
    private function __construct(
        public float $amount,
        public float $paid,
        public float $balance,
        public string $status,
    ) {}

    /**
     * @param  iterable<object{price: float, quantity: float, discount: float, tax: float}>  $billItems
     * @param  iterable<object{debit: float}>  $qualifyingPayments  Already filtered to
     *                                                              qualifying account types + debit > 0 (see Bill::payments()).
     */
    public static function compute(iterable $billItems, iterable $qualifyingPayments): self
    {
        $amount = self::itemsTotal($billItems);
        $paid = self::paymentsTotal($qualifyingPayments);
        $balance = $amount - $paid;

        return new self(
            amount: $amount,
            paid: $paid,
            balance: $balance,
            status: self::classify($balance, $amount),
        );
    }

    /**
     * @param  iterable<object{price: float, quantity: float, discount: float, tax: float}>  $billItems
     */
    public static function itemsTotal(iterable $billItems): float
    {
        $value = 0;
        foreach ($billItems as $item) {
            $itemValue = ($item->price * $item->quantity) - $item->discount;
            $value = $value + ($itemValue + ($itemValue * ($item->tax / 100)));
        }

        return $value;
    }

    /**
     * @param  iterable<object{debit: float}>  $qualifyingPayments
     */
    public static function paymentsTotal(iterable $qualifyingPayments): float
    {
        $paid = 0;
        foreach ($qualifyingPayments as $payment) {
            $paid = $paid + $payment->debit;
        }

        return $paid;
    }

    private static function classify(float $balance, float $amount): string
    {
        if ($balance == 0) {
            return 'PAID';
        }
        if ($balance < 0) {
            return 'OVERPAID';
        }
        if ($balance < $amount) {
            return 'PARTIAL PAID';
        }

        return 'UNPAID';
    }
}
