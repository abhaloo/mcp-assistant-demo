<?php

namespace App\Models;

use App\Traits\EntityTrait;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;

class Period extends Model
{
    use EntityTrait;
    use HasFactory;

    protected $guarded = false;

    /**
     * Reporting Period Status
     */
    const OPEN = 'OPEN';

    const CLOSED = 'CLOSED';

    const ADJUSTING = 'ADJUSTING';
}
