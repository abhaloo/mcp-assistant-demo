<?php

return [

    /*
    |--------------------------------------------------------------------------
    | Badge Color Palette
    |--------------------------------------------------------------------------
    |
    | Unified pastel color definitions for all status badges in the app.
    | Each entry maps a semantic name to a background and text color pair.
    |
    | The "core" set mirrors Bootstrap's semantic names (success, danger, etc.)
    | but uses pastel tones. The "extended" set provides additional hues for
    | departments, work-order statuses, and other domain-specific badges.
    |
    */

    'colors' => [
        // Core semantic colors (pastel equivalents of Bootstrap)
        'success'   => ['bg' => '#d1fae5', 'text' => '#065f46'],
        'danger'    => ['bg' => '#fee2e2', 'text' => '#991b1b'],
        'warning'   => ['bg' => '#fef3c7', 'text' => '#92400e'],
        'info'      => ['bg' => '#dbeafe', 'text' => '#1e40af'],
        'primary'      => ['bg' => '#c7d2fe', 'text' => '#3730a3'],
        'primary-dark' => ['bg' => '#325d88', 'text' => '#ffffff'],
        'secondary' => ['bg' => '#e2e8f0', 'text' => '#334155'],
        'light'     => ['bg' => '#f1f5f9', 'text' => '#475569'],

        // Extended palette
        'pink'      => ['bg' => '#fce7f3', 'text' => '#9d174d'],
        'orange'    => ['bg' => '#ffedd5', 'text' => '#9a3412'],
        'violet'    => ['bg' => '#ddd6fe', 'text' => '#5b21b6'],
        'teal'      => ['bg' => '#ccfbf1', 'text' => '#115e59'],
        'lime'      => ['bg' => '#ecfccb', 'text' => '#3f6212'],
        'sky'       => ['bg' => '#bae6fd', 'text' => '#0c4a6e'],
        'lavender'  => ['bg' => '#e9d5ff', 'text' => '#6b21a8'],
        'yellow'    => ['bg' => '#fef9c3', 'text' => '#854d0e'],
        'indigo'    => ['bg' => '#e0e7ff', 'text' => '#3730a3'],
        'cyan'      => ['bg' => '#99f6e4', 'text' => '#115e59'],
    ],

];
