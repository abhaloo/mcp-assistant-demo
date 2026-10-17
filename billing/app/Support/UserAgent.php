<?php

declare(strict_types=1);

namespace App\Support;

use Detection\MobileDetect;
use Illuminate\Http\Request;

final class UserAgent
{
    private MobileDetect $detect;

    private ?string $userAgent = null;

    public function __construct(?string $userAgent = null)
    {
        $this->detect = new MobileDetect;
        if ($userAgent !== null) {
            $this->setUserAgent($userAgent);
        }
    }

    public static function fromRequest(Request $request): self
    {
        return (new self)->setUserAgent($request->userAgent());
    }

    public function setUserAgent(?string $userAgent): self
    {
        $this->userAgent = $userAgent;
        $this->detect->setUserAgent($userAgent ?? '');

        return $this;
    }

    public function isMobile(): bool
    {
        return $this->userAgent ? $this->detect->isMobile() : false;
    }

    public function isTablet(): bool
    {
        return $this->userAgent ? $this->detect->isTablet() : false;
    }

    public function deviceType(): string
    {
        if ($this->isTablet()) {
            return 'Tablet';
        }
        if ($this->isMobile()) {
            return 'phone';
        }

        return 'Desktop';
    }

    public function browser(): string
    {
        $ua = $this->userAgent ?? '';
        foreach (['Edg' => 'Edge', 'Chrome' => 'Chrome', 'Firefox' => 'Firefox', 'Safari' => 'Safari'] as $needle => $name) {
            if (stripos($ua, $needle) !== false) {
                return $name;
            }
        }

        return 'Unknown';
    }

    public function platform(): string
    {
        $ua = $this->userAgent ?? '';
        if (stripos($ua, 'Android') !== false) {
            return 'Android';
        }
        if (stripos($ua, 'iPhone') !== false || stripos($ua, 'iPad') !== false) {
            return 'iOS';
        }
        if (stripos($ua, 'Windows') !== false) {
            return 'Windows';
        }
        if (stripos($ua, 'Mac OS') !== false) {
            return 'OS X';
        }

        return 'Unknown';
    }
}
