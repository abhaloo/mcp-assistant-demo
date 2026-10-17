<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use InvalidArgumentException;

class AiJobDetailValue extends Model
{
    use HasFactory;

    protected $table = 'ai_job_detail_values';

    protected $guarded = [];

    protected $casts = [
        'value_number' => 'decimal:4',
        'value_boolean' => 'boolean',
        'value_json' => 'array',
        'provenance' => 'array',
        'confidence' => 'decimal:4',
        'source_updated_at' => 'datetime',
        'version' => 'integer',
    ];

    protected static function booted(): void
    {
        static::saving(function (self $value): void {
            if ($value->entity_id === null) {
                throw new InvalidArgumentException('Typed detail entity_id is required.');
            }

            $slots = array_filter([
                $value->value_text,
                $value->value_number,
                $value->value_boolean,
                $value->value_json,
            ], static fn (mixed $slot): bool => $slot !== null);

            if (count($slots) !== 1) {
                throw new \LogicException('A typed detail value must contain exactly one typed value slot.');
            }
        });
    }

    /**
     * @return BelongsTo<WorkOrder, AiJobDetailValue>
     */
    public function workOrder(): BelongsTo
    {
        return $this->belongsTo(WorkOrder::class, 'work_order_id');
    }

    /** @deprecated Use workOrder(). */
    public function job(): BelongsTo
    {
        return $this->workOrder();
    }

    /**
     * @return BelongsTo<AiDefinitionRevision, AiJobDetailValue>
     */
    public function definitionRevision(): BelongsTo
    {
        return $this->belongsTo(AiDefinitionRevision::class, 'definition_revision_id');
    }

    /**
     * @return BelongsTo<Department, AiJobDetailValue>
     */
    public function department(): BelongsTo
    {
        return $this->belongsTo(Department::class, 'department_id');
    }
}
