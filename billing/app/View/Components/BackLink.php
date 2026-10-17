<?php

namespace App\View\Components;

use Illuminate\Contracts\View\View;
use Illuminate\View\Component;

class BackLink extends Component
{
    public string $href;

    /**
     * Link back to the page the user came from, or to $fallback when that page is unknown.
     */
    public function __construct(string $fallback)
    {
        $this->href = $this->previousUrlOr($fallback);
    }

    /**
     * Use the previous URL only when it is a different page on this host and not the login page.
     */
    protected function previousUrlOr(string $fallback): string
    {
        $previous = (string) (request()->headers->get('referer') ?: session()->previousUrl());

        if ($previous === '' || parse_url($previous, PHP_URL_HOST) !== request()->getHost()) {
            return $fallback;
        }

        $ignoredPaths = [$this->normalisePath(request()->path()), $this->normalisePath(route('login', [], false))];

        if (in_array($this->normalisePath((string) parse_url($previous, PHP_URL_PATH)), $ignoredPaths, true)) {
            return $fallback;
        }

        return $previous;
    }

    protected function normalisePath(string $path): string
    {
        return '/'.trim($path, '/');
    }

    /**
     * Get the view / contents that represent the component.
     *
     * @return View|\Closure|string
     */
    public function render()
    {
        return view('components.back-link');
    }
}
