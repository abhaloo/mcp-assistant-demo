<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;

class CreditNote extends Model
{
    use HasFactory;

    protected $guarded = false;

    public function bill()
    {
        return $this->belongsTo(Bill::class);
    }
}
