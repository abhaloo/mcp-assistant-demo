<div>
    <div class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center pt-3 pb-2 mb-3 border-bottom">
        <h1 class="h2">Cash Flow Statement</h1>
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
                        <input type="submit" value="Run" wire:click="filter" class="btn btn-success btn-sm mt-3">
                    </div>

                </div>

            </div>
        </div>
    </div>

    @if($cash_flow_statement)
        <div class="card card-body">
            {{-- Desktop view --}}
            <div class="d-none d-md-block">
                <table class="table table-bordered table-sm">
                    {!! $cash_flow_statement->printLayout() !!}
                </table>
            </div>
            {{-- Mobile view --}}
            <div class="d-md-none">
                <div class="alert alert-info mb-2"><i class="fa fa-info-circle me-1"></i> Scroll horizontally to view full report, or use landscape mode.</div>
                <div class="overflow-auto" style="font-size: 0.75rem;">
                    <table class="table table-bordered table-sm">
                        {!! $cash_flow_statement->printLayout() !!}
                    </table>
                </div>
            </div>
        </div>
    @endif
</div>
