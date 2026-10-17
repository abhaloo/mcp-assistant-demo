<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\Relations\HasMany;
use InvalidArgumentException;

class AiDefinitionRevision extends Model
{
    use HasFactory;

    protected $table = 'ai_definition_revisions';

    protected $guarded = [];

    protected $casts = [
        'is_active' => 'boolean',
        'metadata' => 'array',
        'aliases' => 'array',
        'options' => 'array',
        'revision' => 'integer',
        'published_at' => 'datetime',
        'retired_at' => 'datetime',
    ];

    protected static function booted(): void
    {
        static::saving(function (self $revision): void {
            if ($revision->entity_id === null) {
                throw new InvalidArgumentException('Definition revision entity_id is required.');
            }
        });

        static::updating(function (self $revision): void {
            if ($revision->getOriginal('published_at') !== null) {
                throw new \LogicException('Published definition revisions are immutable.');
            }
        });

        static::deleting(function (self $revision): void {
            if ($revision->published_at !== null) {
                throw new \LogicException('Published definition revisions cannot be deleted.');
            }
        });
    }

    /**
     * @return BelongsTo<AiAttributeFamily, AiDefinitionRevision>
     */
    public function family(): BelongsTo
    {
        return $this->belongsTo(AiAttributeFamily::class, 'attribute_family_id');
    }

    /**
     * @return HasMany<AiJobDetailValue>
     */
    public function values(): HasMany
    {
        return $this->hasMany(AiJobDetailValue::class, 'definition_revision_id');
    }
}
