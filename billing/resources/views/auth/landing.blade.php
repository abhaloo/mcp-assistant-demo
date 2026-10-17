@extends('layouts.auth')
@section('content')
    <div class="d-table-cell align-middle">
        <div class="text-center mt-4">
            <h1 class="h2">Answers from your business records</h1>
            <p class="lead">
                Compare figures, find records, and check company policies, all from one assistant inside the app.
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
                    <p class="text-center mt-3">
                        Finance sees jobs, invoices, and customers. Warehouse sees jobs. Both accounts are read-only.
                    </p>
                    <p class="text-center">
                        Try asking: "Which customers owe us the most right now?"
                    </p>
                    <p class="text-center text-muted small">
                        Demo data is anonymised. Your questions go to external AI services, so don't enter anything confidential.
                    </p>
                </div>
            </div>
        </div>
    </div>
@endsection
