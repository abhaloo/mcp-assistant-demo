<?php

namespace App\Models;

use App\Traits\EntityTrait;
use IFRS\Models\Entity;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;

class Unit extends Model
{
    use EntityTrait;
    use HasFactory;

    protected $fillable = ['name'];

    public function entity()
    {
        return $this->belongsTo(Entity::class);
    }

    public function products()
    {
        return $this->hasMany(Product::class, 'unit_id');
    }

    protected static function booted()
    {
        static::creating(function ($model) {
            static::assignEntityIdIfMissing($model);
        });
    }
}
