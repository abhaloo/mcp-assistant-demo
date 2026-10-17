<?php

namespace App\Traits;

use App\Models\User;
use Illuminate\Database\Eloquent\Builder;

trait EntityTrait
{
    protected static function bootEntityTrait()
    {
        // Loading the authenticated User must not call auth()->check() here (recursive boot).
        if (static::class === User::class) {
            return;
        }

        // Bring of data bellow to user vendor account
        if (auth()->check()) {
            if (! auth()->user()->hasRole(['Super Admin'])) {
                static::addGlobalScope('entities', function (Builder $builder) {
                    $builder->where(
                        $builder->getModel()->qualifyColumn('entity_id'),
                        auth()->user()->entity_id
                    );
                });
            }
        }
    }

    protected static function assignEntityIdIfMissing(object $model): void
    {
        if ($model->entity_id !== null) {
            return;
        }

        $model->entity_id = auth()->user()->entity_id ?? 1;
    }

    protected static function booted()
    {
        static::creating(function ($model) {
            static::assignEntityIdIfMissing($model);
        });
    }
}
