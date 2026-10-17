<?php

use App\Models\ControlNumber;

class ControlNumberGenerator
{
    public function handle(): string
    {
        do {
            $today = date('Y-m-d');
            $last_count = 1;
            $rows_found = ControlNumber::whereDate('created_at', $today)->get();
            if ($rows_found) {
                $last_count = $rows_found->count() + 1;
            }

            // Generate Unique
            $vendor_id = rand(10, 99);
            $current_count = substr(str_repeat(0, 3).$last_count, -3);
            $unique = '05'.$vendor_id.date('mdy').$current_count;

            $found = ControlNumber::where('control_number', $unique)->count();
        } while ($found > 0);

        return $unique;
    }
}
