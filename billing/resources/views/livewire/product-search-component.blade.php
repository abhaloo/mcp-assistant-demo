<div class="position-relative" wire:click.away="resetFields" id="product-search-{{ $this->getId() }}">
    <label for="product-input-{{ $this->getId() }}">{{ is_array($type) ? 'Items' : $type }}</label>
    <div class="input-group">
        <input
            type="text"
            id="product-input-{{ $this->getId() }}"
            class="form-control"
            style="color: #212529 !important;"
            placeholder="Search {{ is_array($type) ? 'Items' : $type }}..."
            wire:model.live="query"
            wire:keydown.escape="resetFields"
            wire:keydown.tab="resetFields"
            wire:keydown.arrow-up="decrementHighlight"
            wire:keydown.arrow-down="incrementHighlight"
            wire:keydown.enter="selectProduct"
        />
        @if(!empty($query))
            <button type="button" class="btn btn-outline-secondary border-start-0" wire:click="resetFields" title="Clear">
                <i class="fa fa-times"></i>
            </button>
        @endif
        <button type="button" class="btn btn-secondary">
            <i class="fa fa-search"></i>
        </button>
    </div>

    <div wire:loading class="position-absolute w-100 text-muted small py-2 px-3 bg-light border" style="z-index: 1050; top: 100%;">
        <i class="fa fa-spinner fa-spin me-1"></i> Searching...
    </div>

    @if(!empty($query))
        <div class="product-search-dropdown position-absolute w-100 bg-white border rounded shadow-sm" style="z-index: 1050; top: 100%; max-height: 280px; overflow-y: auto;">
            @if(!empty($products))
                @foreach($products as $i => $product)
                    <a href="#"
                        wire:click.prevent="selectProduct({{ $product['id'] }})"
                        wire:key="product-{{ $product['id'] }}"
                        class="product-search-item d-flex justify-content-between align-items-center px-3 py-2 text-decoration-none border-bottom {{ $highlightIndex === $i ? 'active' : '' }}"
                    >
                        <span class="text-truncate me-2">{{ $product['name'] }}</span>
                        <x-badge :color="$product['stock_label']['color']" class="flex-shrink-0">
                            {{ $product['stock_label']['text'] }}
                        </x-badge>
                    </a>
                @endforeach
            @else
                <div class="px-3 py-2 text-muted small">
                    <i class="fa fa-exclamation-circle me-1"></i> No results found
                </div>
            @endif
        </div>
    @endif

    {{-- Loading overlay when selecting a product --}}
    <x-loading-overlay target="selectProduct" message="Adding item..." />

    @once
    <style>
        .product-search-item {
            color: #212529;
            background-color: #fff;
            transition: background-color 0.15s ease;
        }
        .product-search-item:hover {
            background-color: #e9ecef;
        }
        .product-search-item.active {
            background-color: var(--bs-primary, #0d6efd);
            color: #fff;
        }
        .product-search-item.active:hover {
            background-color: var(--bs-primary, #0d6efd);
        }
        .product-search-item.active .badge {
            background-color: #fff !important;
            color: #212529 !important;
        }
    </style>
    <script>
        document.addEventListener('livewire:init', function () {
            Livewire.hook('morph.updated', () => {
                const highlighted = document.querySelector('.product-search-dropdown .product-search-item.active');
                if (highlighted) {
                    highlighted.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
                }
            });
        });
    </script>
    @endonce
</div>
