<?php

namespace App\Models;

use App\Models\Concerns\LogsModelActivity;
use App\Traits\EntityTrait;
use Illuminate\Database\Eloquent\Builder;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;

class Journal extends Model
{
    use EntityTrait;
    use HasFactory;
    use LogsModelActivity;

    protected $guarded = false;

    protected $casts = [
        'post_date' => 'datetime',
    ];

    /*
     *  Entry Types
     */
    const DEBIT = 'Debit';

    const CREDIT = 'Credit';

    public function account()
    {
        return $this->belongsTo(Account::class);
    }

    public function inventory()
    {
        return $this->belongsTo(Inventory::class);
    }

    public function currency()
    {
        return $this->belongsTo(Currency::class);
    }

    public function bill()
    {
        return $this->belongsTo(Bill::class);
    }

    public function otherTransaction()
    {
        return $this->with('account')->where('id', '!=', $this->id)
            ->where('document_no', $this->document_no)->first()?->account?->name;
    }

    protected static function boot()
    {
        parent::boot();

        // Filter out cash entries for non-admin users
        // Null-safe check for CLI commands and unauthenticated contexts
        $user = auth()->user();
        if ($user && $user->role !== 'admin') {
            static::addGlobalScope('cash', function (Builder $builder) {
                $builder->where('cash', false);
            });
        }
    }
}
