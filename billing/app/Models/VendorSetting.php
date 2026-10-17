<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;

class VendorSetting extends Model
{
    use HasFactory;

    protected $guarded = false;

    public function customer()
    {
        return $this->belongsTo(Customer::class, 'setting_link');
    }
}
