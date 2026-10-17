<?php

namespace App\Models;

use App\Traits\EntityTrait;
use IFRS\Models\Entity;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

class ControlNumber extends Model
{
    use EntityTrait;
    use HasFactory;

    public function entity()
    {
        return $this->belongsTo(Entity::class);
    }

    protected $casts = [
        'expire_date' => 'datetime',
    ];

    protected $guarded = false;

    public function bill(): BelongsTo
    {
        return $this->belongsTo(Bill::class);
    }

    protected static function booted()
    {
        static::creating(function ($model) {
            if ($model->entity_id === null && auth()->user()) {
                $model->entity_id = auth()->user()->entity_id;
            }
        });
    }
}
