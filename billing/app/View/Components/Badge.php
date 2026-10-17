<?php

namespace App\View\Components;

use App\Helpers\BadgeHelper;
use Illuminate\View\Component;

class Badge extends Component
{
    public string $bg;

    public string $textColor;

    public ?string $icon;

    public function __construct(string $color = 'secondary', ?string $icon = null)
    {
        $palette = BadgeHelper::palette($color);
        $this->bg = $palette['bg'];
        $this->textColor = $palette['text'];
        $this->icon = $icon;
    }

    public function render()
    {
        return view('components.badge');
    }
}
