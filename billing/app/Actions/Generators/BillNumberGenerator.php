<?php

namespace App\Actions\Generators;

use Illuminate\Support\Facades\DB;

class BillNumberGenerator
{
    /**
     * Generate the next bill number based on type.
     * Format: E8295 for Quotation, E6858 for Invoice (all use E prefix)
     *
     * @param  string  $type  'Invoice', 'Quotation', or 'Payable Quotation'
     */
    public static function handle(string $type): string
    {
        // All bill types use E prefix
        $prefix = 'E';

        // Get the maximum numeric portion from existing bills of this type
        $maxNumber = DB::table('bills')
            ->where('type', $type)
            ->whereNotNull('invoice_number')
            ->where('invoice_number', '!=', '')
            ->pluck('invoice_number')
            ->map(function ($number) {
                // Extract numeric portion from E8294 format
                if (preg_match('/^E(\d+)$/', $number, $matches)) {
                    return intval($matches[1]);
                }

                return 0;
            })
            ->max();

        $nextNumber = ($maxNumber ?? 0) + 1;

        return $prefix.$nextNumber;
    }
}
