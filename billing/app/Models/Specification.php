<?php

namespace App\Models;

use App\Traits\EntityTrait;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;

class Specification extends Model
{
    use EntityTrait;
    use HasFactory;

    protected $fillable = ['name'];

    public function items()
    {
        return $this->hasMany(SpecificationItem::class);
    }
}
