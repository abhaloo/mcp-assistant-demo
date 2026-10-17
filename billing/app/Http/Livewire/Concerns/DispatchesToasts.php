<?php

declare(strict_types=1);

namespace App\Http\Livewire\Concerns;

trait DispatchesToasts
{
    protected function toast(string $title, string $text): void
    {
        $this->dispatch('swal:info', title: $title, text: $text);
    }
}
