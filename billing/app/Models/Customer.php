<?php

namespace App\Models;

use App\Models\Concerns\LogsModelActivity;
use App\Traits\EntityTrait;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\HasMany;

class Customer extends Model
{
    use EntityTrait;
    use HasFactory;
    use LogsModelActivity;

    protected $guarded = false;

    public function bills(): HasMany
    {
        return $this->hasMany(Bill::class);
    }

    public function payable_bills(): HasMany
    {
        return $this->hasMany(Bill::class)->whereIn('type', ['Invoice', 'Payable Quotation']);
    }

    public function customerOrders(): HasMany
    {
        return $this->hasMany(CustomerOrder::class);
    }

    public function customerDirectPayment()
    {
        return $this->hasMany(Journal::class)->where('type', 'Journal Entry');
    }

    public function currency()
    {
        return $this->belongsTo(Currency::class);
    }

    public function account()
    {
        return $this->belongsTo(Account::class);
    }

    protected static function booted()
    {
        static::creating(function ($model) {
            static::assignEntityIdIfMissing($model);
        });
    }
}
