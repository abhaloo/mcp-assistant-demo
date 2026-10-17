<?php

declare(strict_types=1);

namespace App\Support;

use Livewire\Component;
use Livewire\Livewire;

final class LivewireInteractionAudit
{
    public static function register(): void
    {
        if (! config('interaction-audit.enabled', false)) {
            return;
        }

        Livewire::listen('action.returned', static function (
            Component $component,
            string $method,
            mixed $returned,
            string $id
        ): void {
            $componentName = class_basename($component);
            $allowlist = config('interaction-audit.livewire_components', []);

            if (! array_key_exists($componentName, $allowlist)) {
                return;
            }

            $allowedMethods = $allowlist[$componentName];

            if (is_array($allowedMethods) && ! in_array($method, $allowedMethods, true)) {
                return;
            }

            InteractionLogger::log('livewire', $componentName . '::' . $method, [
                'component_id' => $id,
            ]);
        });
    }
}
