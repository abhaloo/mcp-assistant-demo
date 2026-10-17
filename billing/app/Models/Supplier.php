<?php

namespace App\Models;

use App\Models\Concerns\LogsModelActivity;
use App\Traits\EntityTrait;
use IFRS\Models\Entity;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;

class Supplier extends Model
{
    use EntityTrait;
    use HasFactory;
    use LogsModelActivity;

    protected $guarded = false;

    public function entity()
    {
        return $this->belongsTo(Entity::class);
    }

    public function currency()
    {
        return $this->belongsTo(Currency::class);
    }

    public function inventories()
    {
        return $this->hasMany(Inventory::class);
    }

    protected static function booted()
    {
        static::creating(function ($model) {
            static::assignEntityIdIfMissing($model);
        });
    }
}
