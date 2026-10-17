<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\SoftDeletes;

class WorkOrderAttachment extends Model
{
    use HasFactory, SoftDeletes;

    protected $guarded = false;

    public static function diskName(): string
    {
        return config('filesystems.artwork_disk', 'artwork_local');
    }

    public function workOrder()
    {
        return $this->belongsTo(WorkOrder::class);
    }

    public function user()
    {
        return $this->belongsTo(User::class);
    }
}
