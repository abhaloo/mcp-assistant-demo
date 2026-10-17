<?php

declare(strict_types=1);

namespace App\Actions\Sidebar;

use App\Authorization\BusinessQuery\Catalog\DefaultUiDestinations;
use App\Authorization\BusinessQuery\Catalog\UiDestination;
use App\Models\Bill;
use Illuminate\Contracts\Auth\Authenticatable;
use Illuminate\Support\Facades\Auth;

class BuildSidebarMenu
{
    /** @return array{quickActions: list<array<string, mixed>>, groups: list<array<string, mixed>>} */
    public function __invoke(?Authenticatable $user = null): array
    {
        $user ??= Auth::user();

        if ($user === null) {
            return ['quickActions' => [], 'groups' => []];
        }

        return [
            'quickActions' => $this->quickActions($user),
            'groups' => array_values(array_filter([
                $this->coreGroup($user),
                $this->accountingGroup($user),
                $this->financialReportsGroup($user),
                $this->generalReportsGroup($user),
                $this->settingsGroup($user),
            ])),
        ];
    }

    /**
     * Flatten active menu item ids for the persist-safe sidebar beacon.
     *
     * @param  array{quickActions?: list<array<string, mixed>>, groups: list<array<string, mixed>>}  $menu
     * @return list<string>
     */
    public static function activeIds(array $menu): array
    {
        $ids = [];

        foreach ($menu['groups'] ?? [] as $group) {
            foreach ($group['items'] ?? [] as $item) {
                if ($item['active'] ?? false) {
                    $ids[] = $item['id'];
                }
            }
        }

        return $ids;
    }

    /** @return list<array<string, mixed>> */
    private function quickActions(Authenticatable $user): array
    {
        return [];
    }

    /** @return array<string, mixed>|null */
    private function coreGroup(Authenticatable $user): ?array
    {
        $items = array_values(array_filter([
            $this->catalogRow($user, 'customer.index', 'customers', 'fa-users', request()->routeIs('customer.index')),
            $this->catalogRow($user, 'bill.invoices', 'invoices', 'fa-file-lines', $this->billNavActive('Invoice')),
            $this->catalogRow($user, 'work-order.index', 'jobs', 'fa-clock', request()->routeIs('work-order.index')),
        ]));

        return $this->group('core', 'Core', $items);
    }

    /** @return array<string, mixed>|null */
    private function accountingGroup(Authenticatable $user): ?array
    {
        return null;
    }

    /** @return array<string, mixed>|null */
    private function financialReportsGroup(Authenticatable $user): ?array
    {
        return null;
    }

    /** @return array<string, mixed>|null */
    private function generalReportsGroup(Authenticatable $user): ?array
    {
        return null;
    }

    /** @return array<string, mixed>|null */
    private function settingsGroup(Authenticatable $user): ?array
    {
        return null;
    }

    /** @return array<string, mixed>|null */
    private function catalogRow(Authenticatable $user, string $key, string $id, string $icon, bool $active): ?array
    {
        $destination = DefaultUiDestinations::byKey($key);
        if (! $this->userCanSeeDestination($user, $destination)) {
            return null;
        }

        return $this->row($id, $destination->label, $this->destinationHref($destination), $icon, $active);
    }

    private function destinationHref(UiDestination $destination): string
    {
        return route($destination->routeName, array_merge($destination->routeParams, $destination->query));
    }

    private function userCanSeeDestination(Authenticatable $user, UiDestination $destination): bool
    {
        if ($destination->linkPermissions === []) {
            return false;
        }

        if ($destination->linkPermissionMode === 'all') {
            foreach ($destination->linkPermissions as $permission) {
                if (! $user->can($permission)) {
                    return false;
                }
            }

            return true;
        }

        return $user->canAny($destination->linkPermissions);
    }

    /** @return array<string, mixed> */
    private function row(string $id, string $label, string $href, string $icon, bool $active): array
    {
        return compact('id', 'label', 'href', 'icon', 'active');
    }

    private function billNavActive(string $navType): bool
    {
        if (! request()->routeIs('bill.*')) {
            return false;
        }

        if (request()->routeIs('bill.index')) {
            return $this->billTypeMatchesNav((string) request()->get('type', 'Invoice'), $navType);
        }

        if (request()->routeIs('bill.create')) {
            $type = request()->get('type');

            return $type !== null && $this->billTypeMatchesNav((string) $type, $navType);
        }

        $bill = request()->route('bill');

        return $bill instanceof Bill && $this->billTypeMatchesNav($bill->type, $navType);
    }

    private function billTypeMatchesNav(string $currentType, string $navType): bool
    {
        if ($navType === 'Invoice') {
            return $currentType === 'Invoice';
        }

        return in_array($currentType, ['Quotation', 'Payable Quotation'], true);
    }

    /**
     * @param  list<array<string, mixed>>  $items
     * @return array<string, mixed>|null
     */
    private function group(string $key, string $label, array $items, bool $closed = false): ?array
    {
        if ($items === []) {
            return null;
        }

        return [
            'key' => $key,
            'label' => $label,
            'closed' => $closed,
            'items' => $items,
        ];
    }
}
