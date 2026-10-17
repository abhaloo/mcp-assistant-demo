<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Builder;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;

class WareHouse extends Model
{
    use HasFactory;

    protected static function booted()
    {
        if (auth()->check()) {

            if (auth()->user()->department_id) {
                static::addGlobalScope('department', function (Builder $builder) {
                    $builder->where('departments', 'like', '%'.auth()->user()->department_id.'%');
                });
            }

        }
    }
}
