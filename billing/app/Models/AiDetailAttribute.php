<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use InvalidArgumentException;

class AiDetailAttribute extends Model
{
    use HasFactory;

    protected $table = 'ai_detail_attributes';

    protected $guarded = [];

    protected static function booted(): void
    {
        static::saving(function (self $attribute): void {
            if ($attribute->entity_id === null) {
                throw new InvalidArgumentException('Detail attribute entity_id is required.');
            }
        });
    }

    protected $casts = [
        'integer_value' => 'integer',
        'numeric_value' => 'decimal:4',
        'boolean_value' => 'boolean',
        'source_updated_at' => 'datetime',
    ];
}
