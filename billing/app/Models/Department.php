<?php

namespace App\Models;

use App\Helpers\BadgeHelper;
use App\Models\Concerns\LogsModelActivity;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;

class Department extends Model
{
    use HasFactory;
    use LogsModelActivity;

    protected $guarded = false;

    /** Department where finished jobs await customer collection. */
    public const PICKUP = 7;

    /** Terminal / non-production departments excluded from "needs attention" tracking. */
    public const NON_ACTIONABLE = [7, 12, 13, 14, 15, 16];

    /**
     * Department badge colors mapped to workflow stage.
     * Spectrum: blue (entry) → indigo (design) → amber/orange (production) → lime (post-prod) → green (complete).
     * Support departments use slate, exception states use muted red/amber/lavender.
     */
    private const BADGE_COLORS = [
        // Entry & design
        3 => 'info',       // SALES — cool blue
        4 => 'indigo',     // GRAPHICS

        // Production (warm tones)
        2 => 'warning',    // PRINTING PRESS — amber
        8 => 'pink',       // TISSUE
        9 => 'orange',     // INDIGO-DIGITAL
        10 => 'danger',     // LARGE FORMAT — coral

        // Post-production
        11 => 'yellow',     // CUTTING
        6 => 'lime',       // FINISHING

        // Complete & delivered
        7 => 'success',    // COMPLETED — soft green
        12 => 'teal',       // DELIVERED&CHARGED — teal (distinct from COMPLETED)
        13 => 'cyan',       // DELIVERED (not charged) — cyan

        // Support departments
        1 => 'light',      // STORE — slate
        5 => 'lavender',   // ACCOUNTING

        // Exception states
        14 => 'danger',     // REJECTED & CANCELLED
        15 => 'orange',     // JOBS NOT CHARGED
        16 => 'lavender',   // COMPLIMENTARY&INHOUSE
    ];

    private const DEFAULT_COLOR = 'light';

    public function getBadgeColor(): array
    {
        $name = self::BADGE_COLORS[$this->id] ?? self::DEFAULT_COLOR;

        return BadgeHelper::palette($name);
    }

    public function users()
    {
        return $this->hasMany(User::class);
    }
}
