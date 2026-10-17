@extends('layouts.auth')
@section('content')
    <div class="d-table-cell align-middle">

        <div class="text-center mt-4">
            <h1 class="h2">Welcome back</h1>
            <p class="lead">
                Sign in to your account to continue
            </p>
        </div>

        <div class="card">
            <div class="card-body">
                <div class="m-sm-4">
                    <div class="text-center mb-2">
                        <img src="{{asset('img/logo/bloomfield.svg')}}" alt="Bloomfield Print Works" class="img-fluid" width="200" height="200" />
                    </div>
                    <form action="{{ route('login') }}" method="POST" id="login-form">
                        @csrf
                        <div class="mb-3">
                            <label class="form-label">Username</label>
                            <input class="form-control @error('username') is-invalid @endif form-control-lg" type="text" name="username" value="{{ old('username') }}" placeholder="Enter your username" required />
                            @error('username')
                            <div class="invalid-feedback"> {{ $message }} </div>
                            @enderror
                        </div>
                        <div class="mb-3">
                            <label class="form-label">Password</label>
                            <input class="form-control @error('password') is-invalid @endif form-control-lg" type="password" name="password" placeholder="Enter your password" required/>
                            @error('password')
                            <div class="invalid-feedback"> {{ $message }} </div>
                            @enderror
                        </div>

                        <div class="text-center mt-3">
                            <button type="submit" class="btn btn-lg btn-primary" data-login-btn>
                                <span data-login-label>Sign in</span>
                                <span data-login-spinner class="d-none"><i class="fa fa-spinner fa-spin"></i> Signing in&hellip;</span>
                            </button>
                            <!-- <button type="submit" class="btn btn-lg btn-primary">Sign in</button> -->
                        </div>
                    </form>
                </div>
            </div>
        </div>

    </div>

    <script>
        document.addEventListener('DOMContentLoaded', function () {
            var form = document.getElementById('login-form');
            if (!form) return;
            form.addEventListener('submit', function () {
                var btn = form.querySelector('[data-login-btn]');
                if (!btn) return;
                var label = btn.querySelector('[data-login-label]');
                var spinner = btn.querySelector('[data-login-spinner]');
                if (label) label.classList.add('d-none');
                if (spinner) spinner.classList.remove('d-none');
                btn.setAttribute('aria-busy', 'true');
                // Disable after the submit is queued so the POST isn't cancelled.
                setTimeout(function () { btn.disabled = true; }, 0);
            });
        });
    </script>
@endsection
