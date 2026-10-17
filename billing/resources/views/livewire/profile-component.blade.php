<div>
    <div class="row">
        <div class="col">
            <h1 class="h3 mb-3">Change <strong>Password</strong></h1>
        </div>
        <!-- Main content -->
        <section class="content">
            <div class="col card">
                @if (session('warning'))
                    <div class="alert alert-danger">
                        {{ session('warning') }}
                    </div>
                @endif
                <div>
                    <div class="card-body">
                        <div class="row justify-content-center">
                            <div class="col-md-2"><label>Current Password</label></div>
                            <div class="col-md-6">
                                <input wire:model.live="current_password" type="password" class="form-control @error('current_password') is-invalid @enderror">
                                @error('current_password')
                                <span class="invalid-feedback">{{ $message }}</span>
                                @enderror
                            </div>
                        </div>
                        <div class="row justify-content-center mt-3">
                            <div class="col-md-2"><label>New Password</label></div>
                            <div class="col-md-6"><input wire:model.live="new_password" type="password" class="form-control"></div>
                        </div>
                        <div class="row justify-content-center mt-3">
                            <div class="col-md-2"><label>Confirm Password</label></div>
                            <div class="col-md-6">
                                <input wire:model.live="confirm_password" type="password" class="form-control @error('confirm_password') is-invalid @enderror">
                                @error('confirm_password')
                                <span class="invalid-feedback">{{ $message }}</span>
                                @enderror
                            </div>
                        </div>
                    </div>
                    <div class="card-footer ">
                        <div class="row justify-content-center">
                            <div class="col-md-2"></div>
                            <div class="col-md-6 justify-content-end">
                                <div class="col-md-5 float-right pr-0 pl-0">
                                    <button wire:click="changePassword" class="btn btn-primary btn-block">Change</button>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </section>
    </div>
</div>
