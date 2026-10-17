<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use InvalidArgumentException;

class AiDetailMigrationLedger extends Model
{
    use HasFactory;

    protected $table = 'ai_detail_migration_ledger';

    protected $guarded = [];

    protected static function booted(): void
    {
        static::saving(function (self $ledger): void {
            if ($ledger->entity_id === null) {
                throw new InvalidArgumentException('Detail migration entity_id is required.');
            }
        });
    }

    protected $casts = [
        'conflict_details' => 'array',
    ];
}
