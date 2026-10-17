<?php

use App\Http\Middleware\AttachRequestContext;

if (! function_exists('percentageDiff')) {
    function percentageDiff($current, $original): array
    {
        if ($original == 0) {
            if ($current == 0) {
                return ['icon' => true, 'value' => 0, 'new' => false, 'label' => '0%'];
            }

            return ['icon' => true, 'value' => null, 'new' => true, 'label' => 'New'];
        }

        $diff = $current - $original;
        $more_less = $diff > 0;
        $percentChange = abs($diff / $original) * 100;
        $roundedRaw = round($percentChange);
        $rounded = min($roundedRaw, 999);

        return [
            'icon' => $more_less,
            'value' => $rounded,
            'new' => false,
            'label' => ($roundedRaw > 999 ? '999+' : number_format($rounded)).'%',
        ];
    }

}

if (! function_exists('shortMoney')) {
    function shortMoney($n): string
    {
        $n = (float) $n;
        $abs = abs($n);

        if ($abs >= 1_000_000_000) {
            return round($n / 1_000_000_000, 1).'B';
        }
        if ($abs >= 1_000_000) {
            return round($n / 1_000_000, 1).'M';
        }
        if ($abs >= 1_000) {
            return round($n / 1_000, 1).'K';
        }

        return number_format($n, 0);
    }
}

if (! function_exists('abbrvWords')) {
    function abbrvWords($word): string
    {
        if (preg_match_all('/\b(\w)/', strtoupper($word), $m)) {
            return implode('', $m[1]);
        }

        return '';
    }
}

if (! function_exists('numPat')) {
    function numPat($input, $pad_len = 7, $prefix = null)
    {
        if ($pad_len <= strlen($input)) {
            trigger_error('<strong>$pad_len</strong> cannot be less than or equal to the length of <strong>$input</strong> to generate pattern number', E_USER_ERROR);
        }

        if (is_string($prefix)) {
            return sprintf('%s%s', $prefix, str_pad($input, $pad_len, '0', STR_PAD_LEFT));
        }

        return str_pad($input, $pad_len, '0', STR_PAD_LEFT);
    }
}

if (! function_exists('request_ref_id')) {
    function request_ref_id(): ?string
    {
        return AttachRequestContext::refId();
    }
}
