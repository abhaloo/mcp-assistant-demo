<div>
    <div class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center py-4">
        <div class="d-block mb-4 mb-md-0">
            <h2 class="h4">{{ $vendor->name }} Setup</h2>
        </div>
    </div>
    <div class="card card-body border-0 shadow table-wrapper table-responsive">
        @forelse($vendor->settings as $setting)
            <div class="row">
                <div class="col-4 text-end">
                    {{ ucfirst(str_replace('_', ' ', $setting->setting_name)) }}:
                </div>
                <div class="col-4 text-start mb-1">
                    @switch($setting->setting_type)
                        @case("decimal")
                        <input type="text" wire:model.live="settings.{{$setting->setting_name}}" step="0.01" class="form-control">
                        @break

                        @case("link")
                        <select class="form-control" wire:model.live="settings.{{$setting->setting_name}}">
                            <!-- Add model relation here for new dropdown settings -->
                            @if($setting->setting_model == 'PaymentProvider')
                              <option value="{{ $setting->setting_link }}"> {{ $setting->paymentProvider->name}}</option>
                            @endif

                            @if($setting->setting_model == 'Customer')
                                <option value="{{ $setting->setting_link }}"> {{ $setting->customer->name}}</option>
                            @endif
                        </select>
                        @break

                        @case("asset")
                        <input type="file" class="form-control" wire:model.live="logo">
                          @error('logo') <span class="text-danger">{{ $message }}</span> @enderror
                        @break

                        @case("boolean")
                        <select class="form-control" wire:model.live="settings.{{$setting->setting_name}}">
                            <option value="yes">Yes</option>
                            <option value="no">No</option>
                        </select>
                        @break

                        @case("options")
                        <select class="form-control" wire:model.live="settings.{{$setting->setting_name}}">
                            <option>Products</option>
                            <option>Services</option>
                            <option>Services & Products</option>
                            <option>Services & Products & Production</option>
                        </select>
                        @break

                    @endswitch
                </div>

                <div class="col-4">
                    @if ($logo && $loop->index == 0)
                        <div style="position: absolute; z-index: 1000">
                            Logo Preview:
                            <img  src="{{ $logo->temporaryUrl() }}" width="100" height="100">
                        </div>
                    @endif
                </div>
            </div>
        @empty
        @endforelse
        <div class="row mt-3">
            <div class="col-4"></div>
            <div class="col-4 text-start">
                <input type="submit" class="btn btn-primary" wire:click="save" value="Save Changes">
            </div>
        </div>

    </div>
</div>
