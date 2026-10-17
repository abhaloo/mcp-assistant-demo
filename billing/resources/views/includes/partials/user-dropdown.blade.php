{{-- User dropdown — opens from the user chip in topbar.blade.php.
     Routes: user.setting, logout. Logout uses the CSRF POST flow that
     was previously in includes/menu.blade.php's footer pod. --}}
@php
    $name = auth()->user()->name ?? 'User';
    $email = auth()->user()->email ?? '';
    $initials = str($name)
        ->explode(' ')
        ->take(2)
        ->map(fn ($w) => str($w)->substr(0, 1))
        ->implode('');
@endphp

<div class="dropdown-menu dropdown-menu-end mcp-topbar__user-menu" role="menu">
    <div class="ud-header">
        <span class="avatar" aria-hidden="true">{{ strtoupper($initials) }}</span>
        <div class="ud-header__text">
            <div class="ud-header__name">{{ $name }}</div>
            @if($email)
                <div class="ud-header__email">{{ $email }}</div>
            @endif
        </div>
    </div>

    <div class="ud-section">
        <form method="POST" action="{{ route('logout') }}" class="d-none" id="topbarLogoutForm">
            @csrf
        </form>
        <a href="{{ route('logout') }}"
           class="ud-row danger"
           role="menuitem"
           onclick="event.preventDefault(); document.getElementById('topbarLogoutForm').submit();">
            <i class="ic fa fa-arrow-right-from-bracket" aria-hidden="true"></i>
            <span>Log out</span>
        </a>
    </div>
</div>
