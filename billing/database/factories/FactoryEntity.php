<?php

namespace Database\Factories;

use App\Models\Entity;

/**
 * Resolves an entity id for factories under MySQL + RefreshDatabase.
 *
 * Hardcoding `entity_id => 1` breaks once AUTO_INCREMENT advances (MySQL does not
 * roll AI back with test transactions). Prefer an existing row; create one if none.
 */
final class FactoryEntity
{
    public static function id(): int
    {
        $existing = Entity::query()->value('id');

        if ($existing !== null) {
            return (int) $existing;
        }

        return (int) Entity::factory()->create()->id;
    }
}
