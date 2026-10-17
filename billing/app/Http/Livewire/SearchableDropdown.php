<?php

namespace App\Http\Livewire;

use Livewire\Component;

/**
 * A generic searchable dropdown component that can be used with any model.
 *
 * Usage examples:
 *
 * For accounts:
 *
 * @livewire('searchable-dropdown', [
 *     'model' => 'App\\Models\\Account',
 *     'searchFields' => ['name', 'code'],
 *     'displayField' => 'name',
 *     'label' => 'Account',
 *     'eventName' => 'accountSelected',
 * ])
 *
 * For categories:
 * @livewire('searchable-dropdown', [
 *     'model' => 'App\\Models\\Category',
 *     'searchFields' => ['name'],
 *     'displayField' => 'name',
 *     'label' => 'Category',
 *     'eventName' => 'categorySelected',
 * ])
 */
class SearchableDropdown extends Component
{
    // Search state
    public $query = '';

    public $items = [];

    public $selectedItem = null;

    public $isOpen = false;

    // Configuration (set via mount)
    public $model;

    public $searchFields = ['name'];

    public $displayField = 'name';

    public $secondaryField = null; // Optional secondary display field (e.g., 'code')

    public $label = 'Select';

    public $placeholder = 'Type to search...';

    public $required = false;

    public $eventName = 'itemSelected';

    public $clearEventName = 'itemCleared';

    public $resetEventName = null; // Auto-generated if not provided

    public $whereConditions = []; // Additional where conditions: [['column', 'operator', 'value'], ...]

    public $orderBy = 'name';

    public $orderDirection = 'asc';

    public $limit = 10;

    public $minChars = 1; // Minimum characters before searching

    public function mount(
        string $model,
        array $searchFields = ['name'],
        string $displayField = 'name',
        ?string $secondaryField = null,
        string $label = 'Select',
        string $placeholder = 'Type to search...',
        bool $required = false,
        string $eventName = 'itemSelected',
        string $clearEventName = 'itemCleared',
        ?string $resetEventName = null,
        array $whereConditions = [],
        string $orderBy = 'name',
        string $orderDirection = 'asc',
        int $limit = 10,
        int $minChars = 1
    ) {
        $this->model = $model;
        $this->searchFields = $searchFields;
        $this->displayField = $displayField;
        $this->secondaryField = $secondaryField;
        $this->label = $label;
        $this->placeholder = $placeholder;
        $this->required = $required;
        $this->eventName = $eventName;
        $this->clearEventName = $clearEventName;
        $this->resetEventName = $resetEventName ?? 'reset'.class_basename($model).'Search';
        $this->whereConditions = $whereConditions;
        $this->orderBy = $orderBy;
        $this->orderDirection = $orderDirection;
        $this->limit = $limit;
        $this->minChars = $minChars;

        // Dynamically register the reset listener
        $this->listeners[$this->resetEventName] = 'resetFields';
    }

    protected function getListeners()
    {
        return [
            $this->resetEventName => 'resetFields',
        ];
    }

    public function resetFields()
    {
        $this->query = '';
        $this->items = [];
        $this->selectedItem = null;
        $this->isOpen = false;
    }

    /**
     * Toggle the dropdown open/closed.
     * When opening, load all items (up to limit).
     */
    public function toggleDropdown()
    {
        $this->isOpen = ! $this->isOpen;

        if ($this->isOpen && empty($this->selectedItem)) {
            $this->loadItems();
        }
    }

    /**
     * Open dropdown and load items.
     */
    public function openDropdown()
    {
        if (! $this->isOpen && empty($this->selectedItem)) {
            $this->isOpen = true;
            $this->loadItems();
        }
    }

    /**
     * Load items based on current query (or all if query is empty).
     */
    protected function loadItems()
    {
        $modelClass = $this->model;
        $queryBuilder = $modelClass::query();

        // Apply search conditions if query exists
        if (! empty($this->query) && strlen($this->query) >= $this->minChars) {
            $queryBuilder->where(function ($q) {
                foreach ($this->searchFields as $index => $field) {
                    if ($index === 0) {
                        $q->where($field, 'like', "%{$this->query}%");
                    } else {
                        $q->orWhere($field, 'like', "%{$this->query}%");
                    }
                }
            });
        }

        // Apply additional where conditions
        foreach ($this->whereConditions as $condition) {
            if (count($condition) === 2) {
                $queryBuilder->where($condition[0], $condition[1]);
            } elseif (count($condition) === 3) {
                $queryBuilder->where($condition[0], $condition[1], $condition[2]);
            }
        }

        $this->items = $queryBuilder
            ->orderBy($this->orderBy, $this->orderDirection)
            ->limit($this->limit)
            ->get()
            ->map(function ($item) {
                return [
                    'id' => $item->id,
                    'display' => $item->{$this->displayField},
                    'secondary' => $this->secondaryField ? $item->{$this->secondaryField} : null,
                ];
            })
            ->toArray();
    }

    public function selectItem($itemId)
    {
        $modelClass = $this->model;
        $item = $modelClass::find($itemId);

        if ($item) {
            $displayValue = $item->{$this->displayField};
            $secondaryValue = $this->secondaryField ? $item->{$this->secondaryField} : null;

            $this->selectedItem = [
                'id' => $item->id,
                'display' => $displayValue,
                'secondary' => $secondaryValue,
            ];

            $this->query = $secondaryValue
                ? "{$displayValue} ({$secondaryValue})"
                : $displayValue;

            $this->items = [];
            $this->isOpen = false;
            $this->dispatch($this->eventName, id: $item->id);
        }
    }

    public function clearSelection()
    {
        $this->query = '';
        $this->selectedItem = null;
        $this->items = [];
        $this->isOpen = false;
        $this->dispatch($this->clearEventName);
    }

    public function closeDropdown()
    {
        $this->items = [];
        $this->isOpen = false;
    }

    public function updatedQuery()
    {
        $this->selectedItem = null;
        $this->isOpen = true;
        $this->loadItems();
    }

    public function render()
    {
        return view('livewire.searchable-dropdown');
    }
}
