@extends('layouts.app')

@section('content')
    <div class="row">
        <div class="col">
            <h1 class="h3 mb-3">User <strong>Permission</strong></h1>
        </div>
        <div class="col">
            <a href="{{ route('user.index') }}"  class="btn btn-sm btn-primary float-end">
                <span class="fa fa-step-backward me-2"></span> Back
            </a>
        </div>
    </div>

    <div class="card card-body border-0 shadow table-wrapper table-responsive">
        <form action="{{ route("store.permission", $user) }}" method="POST">
            @csrf
            <div class="accordion" id="accordionExample">
                @foreach($groups as $group)
                    <div class="card my-2 border border-primary">
                        <div class="border border-light-primary d-flex justify-content-between align-items-center px-2" id="heading{{ $loop->index }}" style="background: #070A50">
                            <h5 class="m-2 text-white-50">
                                <i class="fa fa-arrow-alt-circle-down"></i> {{ ucfirst($group->group) }}
                            </h5>
                            <div class="form-check">
                                <input type="checkbox" class="form-check-input select-all-toggle" data-group="{{ $loop->index }}" id="selectAll{{ $loop->index }}">
                                <label class="form-check-label text-white-50 small" for="selectAll{{ $loop->index }}">Select All</label>
                            </div>
                        </div>
                        <div class="row">
                            <div class="card-body row permission-group" data-group="{{ $loop->index }}">
                                @foreach($permissions as $permission)
                                    @if( $group->group == $permission->group)
                                        <div class="col-12 col-sm-6 col-md-3 col-lg-3 py-0 ml-3 d-block">
                                            <input type="checkbox"
                                                   @foreach($user->permissions as $userPermission)
                                                   @if ($userPermission->name == $permission->name)
                                                   checked
                                                   @endif
                                                   @endforeach
                                                   id="{{$permission->id}}"
                                                   value="{{$permission->name}}" name="permissions[]">
                                            <label for="{{$permission->id}}"> {{ucfirst($permission->name)}}</label>
                                        </div>
                                    @endif
                                @endforeach
                            </div>
                        </div>
                    </div>
                @endforeach
            </div>

            <div class="row mt-3">
                <div class="col-12">
                    <button type="submit" class="btn btn-primary pull-right">
                        <i class="fa fa-refresh"></i> Sync Permission
                    </button>
                </div>
            </div>
        </form>
    </div>
@endsection

@push('scripts')
<script>
    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('.select-all-toggle').forEach(function (toggle) {
            var group = toggle.dataset.group;
            var container = document.querySelector('.permission-group[data-group="' + group + '"]');
            var checkboxes = container.querySelectorAll('input[type="checkbox"]');

            // Set initial state based on whether all are already checked
            toggle.checked = checkboxes.length > 0 && Array.from(checkboxes).every(function (cb) { return cb.checked; });

            // Toggle all on click
            toggle.addEventListener('change', function () {
                checkboxes.forEach(function (cb) { cb.checked = toggle.checked; });
            });

            // Update toggle when individual checkboxes change
            checkboxes.forEach(function (cb) {
                cb.addEventListener('change', function () {
                    toggle.checked = Array.from(checkboxes).every(function (c) { return c.checked; });
                });
            });
        });
    });
</script>
@endpush
