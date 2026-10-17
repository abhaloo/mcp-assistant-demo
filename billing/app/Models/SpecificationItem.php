<?php

namespace App\Models;

use App\Traits\EntityTrait;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;

class SpecificationItem extends Model
{
    use EntityTrait;
    use HasFactory;

    protected $fillable = ['name', 'value', 'type', 'specification_id', 'columns', 'department_id', 'group_name', 'sort_order'];

    public function specification()
    {
        return $this->belongsTo(Specification::class);
    }

    public function department()
    {
        return $this->belongsTo(Department::class);
    }

    /**
     * Check if the given user can edit this specification item.
     * Admins can always edit.
     * If no department is set, any user with 'edit job' can edit.
     * If a department is set, only users in that department can edit.
     *
     * @param  User  $user
     */
    public function canBeEditedBy($user): bool
    {
        // Admins can always edit
        if ($user->role === 'Admin') {
            return true;
        }

        if (empty($this->department_id)) {
            return true; // No specific department required
        }

        return $user->department_id === $this->department_id;
    }
}
