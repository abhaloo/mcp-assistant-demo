<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\HasMany;
use InvalidArgumentException;

class AiAttributeFamily extends Model
{
    use HasFactory;

    protected $table = 'ai_attribute_families';

    protected $guarded = [];

    protected static function booted(): void
    {
        static::saving(function (self $family): void {
            if ($family->entity_id === null) {
                throw new InvalidArgumentException('Attribute family entity_id is required.');
            }
        });
    }

    /**
     * @return HasMany<AiDefinitionRevision>
     */
    public function revisions(): HasMany
    {
        return $this->hasMany(AiDefinitionRevision::class, 'attribute_family_id');
    }
}
