@extends('layouts.auth')
@section('content')
    <div class="d-table-cell align-middle">
        <div class="text-center mt-4">
            <h1 class="h2">{{ config('app.name') }}</h1>
            <p class="lead">
                Questions are screened by a third party.
            </p>
        </div>

        <div class="card">
            <div class="card-body">
                <div class="m-sm-4">
                    <div class="d-grid gap-3 col-10 mx-auto text-center mt-3">
                        <form action="{{ url('demo/enter/finance') }}" method="POST">
                            @csrf
                            <button type="submit" class="btn btn-lg btn-primary w-100">
                                Try as Finance
                            </button>
                        </form>
                        <form action="{{ url('demo/enter/warehouse') }}" method="POST">
                            @csrf
                            <button type="submit" class="btn btn-lg btn-outline-primary w-100">
                                Try as Warehouse
                            </button>
                        </form>
                    </div>
                </div>
            </div>
        </div>
    </div>
@endsection
