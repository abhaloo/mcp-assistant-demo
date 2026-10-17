<?php

namespace App\Actions\Generators;

use App\Models\Journal;

class ReceiptNumberGenerator
{
    public static function handle(): string
    {
        do {
            $today = date('Y-m-d');
            $last_count = 1;
            $rows_found = Journal::whereDate('created_at', $today)->get();
            if ($rows_found) {
                $last_count = $rows_found->count() + 1;
            }

            // Generate Unique
            $current_count = substr(str_repeat(0, 3).$last_count, -3);
            $unique = 'R'.auth()->id().rand(10, 99).date('Ymd').$current_count;

            $found = Journal::where('receipt_number', $unique)->count();
        } while ($found > 0);

        return $unique;
    }
}
