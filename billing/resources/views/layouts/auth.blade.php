<!DOCTYPE html>
<html lang="en">

<head>
    <meta http-equiv="Content-Type" content="text/html; charset=utf-8" />
    <!-- Primary Meta Tags -->
    <title>Bloomfield Print Works</title>
    <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no">
    <!-- Volt CSS -->
    <link rel="canonical" href="https://demo-basic.adminkit.io/pages-sign-in.html" />
    <link type="text/css" href="{{ asset('css/coreui.min.css') }}" rel="stylesheet">
	<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600&display=swap" rel="stylesheet">

    <!-- Font awesome-->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.1.2/css/all.min.css">

    <style>
        .simplebar-scrollbar::before {
            background-color: #ffffff;
        }
    </style>
</head>

<body>
    <main class="d-flex w-100">
        <div class="container d-flex flex-column">
            <div class="row vh-100">
                <div class="col-sm-10 col-md-8 col-lg-6 mx-auto d-table h-100">
                    @yield('content')
                </div>
            </div>
        </div>
    </main>

<!-- Core -->
<script src="{{ asset('js/app.js') }}"></script>
</body>

</html>
