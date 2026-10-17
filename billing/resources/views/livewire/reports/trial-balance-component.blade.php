<div>
    {{-- Loading Overlay --}}
    <x-loading-overlay target="filter" message="Generating trial balance..." />
    <div class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center pt-3 pb-2 mb-3 border-bottom">
        <h1 class="h2">Trial Balance</h1>
        <div class="btn-toolbar mb-2 mb-md-0">
            <div class="form-inline">
                <div class="row">
                    <div class="col">
                        <label for="startDate">Start</label>
                        <input type="date" wire:model.live="from_date" class="form-control form-control-sm">
                    </div>
                    <div class="col">
                        <label for="startDate">End</label>
                        <input type="date" wire:model.live="to_date" class="form-control form-control-sm">
                    </div>

                    <div class="col mt-1">
                        <x-loading-button 
                            wire:click="filter" 
                            target="filter" 
                            label="Run" 
                            icon="fa-play"
                            loading-label="Running..."
                            class="btn btn-success btn-sm mt-3"
                        />
                    </div>

                </div>

            </div>
        </div>
    </div>

    @if($statement)
        <div class="card card-body">
            <p>Showing transactions from <strong>{{ $from_date }}</strong> to <strong>{{ $to_date }}</strong> </p>
            {{-- Desktop view --}}
            <div class="d-none d-md-block">
                <table class="table table-bordered table-sm">
                 {!! $statement->printSections() !!}
                </table>
            </div>
            {{-- Mobile view --}}
            <div class="d-md-none">
                <div class="alert alert-info mb-2"><i class="fa fa-info-circle me-1"></i> Scroll horizontally to view full report, or use landscape mode.</div>
                <div class="overflow-auto" style="font-size: 0.75rem;">
                    <table class="table table-bordered table-sm">
                     {!! $statement->printSections() !!}
                    </table>
                </div>
            </div>
        </div>
    @endif
</div>
