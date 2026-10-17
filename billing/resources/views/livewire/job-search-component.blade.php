<div class="position-relative" wire:click.away="closeDropdown" id="job-search-{{ $this->getId() }}">
    <label for="job-input-{{ $this->getId() }}" class="form-label">{{ $label }}</label>
    <div class="input-group input-group-sm">
        <input
            type="text"
            id="job-input-{{ $this->getId() }}"
            class="form-control"
            style="color: #212529 !important;"
            placeholder="{{ $placeholder }}"
            wire:model.live="query"
            autocomplete="off"
        />
        @if($selectedJob)
            <button type="button" class="btn btn-outline-secondary" wire:click="clearSelection" title="Clear">
                <i class="fa fa-times"></i>
            </button>
        @else
            <button type="button" class="btn btn-secondary">
                <i class="fa fa-search"></i>
            </button>
        @endif
    </div>

    <div wire:loading wire:target="query" class="position-absolute w-100 text-muted small py-2 px-3 bg-light border" style="z-index: 1050; top: 100%;">
        <i class="fa fa-spinner fa-spin me-1"></i> Searching...
    </div>

    @if(!empty($query) && empty($selectedJob) && !empty($jobs))
        <div class="job-search-dropdown position-absolute bg-white border rounded shadow-sm" style="z-index: 1050; top: 100%; min-width: 400px; max-height: 280px; overflow-y: auto; white-space: nowrap;">
            @foreach($jobs as $job)
                <a href="#"
                    wire:click.prevent="selectJob({{ $job['id'] }})"
                    wire:key="job-{{ $job['id'] }}"
                    class="job-search-item d-flex justify-content-between align-items-center px-3 py-2 text-decoration-none border-bottom"
                >
                    <span class="me-3">
                        <strong>{{ $job['work_number'] }}</strong>
                        <span class="text-muted mx-1">-</span>
                        {{ $job['title'] }}
                    </span>
                    <x-mcp.status :kind="\App\Models\WorkOrder::statusKindFor($job['status'])" size="sm" :label="\App\Models\WorkOrder::statusLabelFor($job['status'])" />
                </a>
            @endforeach
        </div>
    @endif

    @once
    <style>
        .job-search-item {
            color: #212529 !important;
            background-color: #fff;
            transition: background-color 0.15s ease;
        }
        .job-search-item:hover {
            background-color: #e9ecef;
            color: #212529 !important;
        }
    </style>
    @endonce
</div>
