<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;

class Entity extends Model
{
    use HasFactory;

    protected $casts = [
        // The account's Ask AI spend limit, USD per month; decimal string so
        // number_format() gets dollars without float drift.
        'ask_budget_usd' => 'decimal:2',
    ];

    protected $fillable = [
        'ask_budget_usd',
    ];

    public function settings()
    {
        return $this->hasMany(VendorSetting::class);
    }

    public function getSetting($name)
    {
        return $this->settings()->where('setting_name', $name)->first()->setting_value;
    }
}
