<?php

namespace App\Models;

// use Illuminate\Contracts\Auth\MustVerifyEmail;
use App\Models\Concerns\LogsModelActivity;
use App\Traits\EntityTrait;
use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Foundation\Auth\User as Authenticatable;
use Illuminate\Notifications\Notifiable;
use Illuminate\Support\Str;
use Laravel\Sanctum\HasApiTokens;
use Spatie\Activitylog\LogOptions;
use Spatie\Permission\Traits\HasPermissions;
use Spatie\Permission\Traits\HasRoles;

class User extends Authenticatable
{
    use EntityTrait, HasApiTokens, HasFactory, HasPermissions,
        HasRoles, LogsModelActivity, Notifiable;

    /**
     * The attributes that are mass assignable.
     *
     * @var array<int, string>
     */
    protected $fillable = [
        'name',
        'username',
        'phone',
        'role',
        'email',
        'password',
        'entity_id',
        'department_id',
    ];

    /**
     * The attributes that should be hidden for serialization.
     *
     * @var array<int, string>
     */
    protected $hidden = [
        'password',
        'remember_token',
    ];

    /**
     * The attributes that should be cast.
     *
     * @var array<string, string>
     */
    protected $casts = [
        'email_verified_at' => 'datetime',
    ];

    public function entity()
    {
        return $this->belongsTo(Entity::class);
    }

    public function department()
    {
        return $this->belongsTo(Department::class);
    }

    public function getShortNameAttribute(): string
    {
        $parts = preg_split('/\s+/', trim($this->name ?? ''), -1, PREG_SPLIT_NO_EMPTY);
        if (count($parts) === 0) {
            return '—';
        }
        if (count($parts) === 1) {
            return $parts[0];
        }

        return $parts[0].' '.Str::substr(end($parts), 0, 1).'.';
    }

    /**
     * Canonical role-name resolution: prefer the first Spatie role, then the
     * legacy `role` column, defaulting to 'user'. Single source of truth for
     * JWT claims, AI rate-limit keys, and telemetry. See
     * docs/superpowers/specs/2026-06-12-global-search-rag-integration-design.md §8.1.
     */
    public function primaryRoleName(): string
    {
        return $this->roles->first()?->name ?? $this->role ?? 'user';
    }

    public function getActivitylogOptions(): LogOptions
    {
        return LogOptions::defaults()
            ->logFillable()
            ->logExcept(['password', 'remember_token'])
            ->logOnlyDirty()
            ->dontSubmitEmptyLogs()
            ->useLogName($this->getTable())
            ->setDescriptionForEvent(fn (string $eventName) => class_basename($this).' '.$eventName);
    }
}
