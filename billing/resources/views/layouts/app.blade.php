<!DOCTYPE HTML>
<html lang="en">

@php
    // Single source of truth for this request: the Ask AI panel include
    // below inherits this same value, so the rendered stream URL and the
    // assets loaded here can never disagree with each other.
    $askV2Enabled = $askV2Enabled ?? (bool) (config('services.rag.ask_v2_enabled', false) || config('ask_ai.ask_v2_enabled', false));
@endphp

<head>
    <meta charset="utf-8">
    <meta http-equiv="X-UA-Compatible" content="IE=edge">
    <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no">
    <meta name="description" content="Printing Press Billing System">
    <meta name="author" content="Bloomfield Print Works">
    <meta name="keywords" content="Printing Press Billing System">
    <meta name="csrf-token" content="{{ csrf_token() }}">
    @auth
        <meta name="user-id" content="{{ auth()->id() }}">
    @endauth
    <meta name="theme-color" content="#090c5f">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">

    {{-- Sentry browser SDK: front-end error capture (renders only when SENTRY_JS_DSN is set). --}}
    @include('includes.sentry-browser')

    <link rel="manifest" href="{{ asset('manifest.json') }}">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link rel="preconnect" href="https://cdnjs.cloudflare.com" crossorigin>
    <link rel="shortcut icon" href="{{ asset('img/logo/logo.gif') }}" />
    <link rel="apple-touch-icon" href="{{ asset('icons/icon-192.png') }}">
    <link rel="preload" as="image" href="{{ asset('img/loaders/ai-chat-loader.svg') }}" type="image/svg+xml">

    {{--
    <link rel="stylesheet" href="{{ asset('css/job.css') }}">--}}
    <link rel="stylesheet" href="{{ asset('css/simplebar.css') }}" media="print" onload="this.media='all'">
    <script defer src="https://cdn.jsdelivr.net/npm/simplebar@latest/dist/simplebar.min.js"></script>

    <title>@unless(app()->environment('production'))[TEST] @endunless Bloomfield Print Works Billing System</title>

    <link href="{{ asset('css/coreui.purged.css')}}" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=Barlow+Condensed:wght@500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet"
        media="print" onload="this.media='all'">
    {{-- Roboto — async like Inter to avoid render-blocking the critical path. --}}
    <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap" rel="stylesheet"
        media="print" onload="this.media='all'">
    {{-- Source Sans 3 — table body font; async like Inter. --}}
    <link href="https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600;700&display=swap" rel="stylesheet"
        media="print" onload="this.media='all'">
    <link rel="stylesheet" href="{{ asset('vendor/sweetalert2/dist/sweetalert2.min.css') }}" media="print"
        onload="this.media='all'">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.1.2/css/all.min.css"
        integrity="sha512-1sCRPdkRXhBV2PBLUdRb4tMg1w2YPf37qatUFeS7zlBy7jJI8Lf4VHwWfZZfpXtYSLy85pkm9GaYVYMfw5BC1A=="
        crossorigin="anonymous" referrerpolicy="no-referrer" media="print" onload="this.media='all'">
    <link rel="stylesheet" href="{{ asset('css/mcp-tokens.css') }}">
    <link rel="stylesheet" href="{{asset('css/simple.purged.css')}}">
    <link rel="stylesheet" href="{{asset('css/loading.css')}}" media="print" onload="this.media='all'">
    <link rel="stylesheet" href="{{ asset('css/mcp-sidebar.css') }}">
    <link rel="stylesheet" href="{{ asset('css/mcp-system.css') }}">
    <link rel="stylesheet" href="{{ asset('css/mcp-dropdown.css') }}">
    <link rel="stylesheet" href="{{ asset('css/mcp-ask-ai-sidebar.css') }}">
    {{-- The panel's own styles load in every mode. The v2 sheet styles the
         optional modules layered on top of it. --}}
    <link rel="stylesheet" href="{{ asset('css/mcp-ask-ai.css') }}">
    <link rel="stylesheet" href="{{ asset('css/mcp-ask-ai-rich-output.css') }}">
    <link rel="stylesheet" href="{{ asset('css/mcp-ask-ai-feedback.css') }}">
    <link rel="stylesheet" href="{{ asset('css/mcp-ask-ai-composer.css') }}">
    @if($askV2Enabled)
        <link rel="stylesheet" href="{{ asset('css/mcp-ask-v2.css') }}">
    @endif
    @livewireStyles
    @stack('styles')

    <noscript>
        <link rel="stylesheet" href="{{ asset('css/simplebar.css') }}">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=Barlow+Condensed:wght@500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
        <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&display=swap" rel="stylesheet">
        <link href="https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600;700&display=swap" rel="stylesheet">
        <link rel="stylesheet" href="{{ asset('vendor/sweetalert2/dist/sweetalert2.min.css') }}">
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.1.2/css/all.min.css"
            integrity="sha512-1sCRPdkRXhBV2PBLUdRb4tMg1w2YPf37qatUFeS7zlBy7jJI8Lf4VHwWfZZfpXtYSLy85pkm9GaYVYMfw5BC1A=="
            crossorigin="anonymous" referrerpolicy="no-referrer">
        <link rel="stylesheet" href="{{asset('css/loading.css')}}">
        <style>
            /**
            * Reinstate scrolling for non-JS clients
            */
            .simplebar-content-wrapper {
                scrollbar-width: auto;
                -ms-overflow-style: auto;
            }

            .simplebar-content-wrapper::-webkit-scrollbar,
            .simplebar-hide-scrollbar::-webkit-scrollbar {
                display: initial;
                width: initial;
                height: initial;
            }
        </style>
    </noscript>

</head>

<body @unless(app()->environment('production'))class="env-staging"@endunless>

    <div class="mcp-loading-bar" data-loading-bar aria-hidden="true">
        <span class="mcp-loading-bar__plate mcp-loading-bar__plate--c"></span>
        <span class="mcp-loading-bar__plate mcp-loading-bar__plate--m"></span>
        <span class="mcp-loading-bar__plate mcp-loading-bar__plate--y"></span>
        <span class="mcp-loading-bar__plate mcp-loading-bar__plate--k"></span>
    </div>

    {{-- Non-persisted beacon: wire:navigate keeps @persist sidebar DOM, so active
         state is synced client-side from these server-computed ids. --}}
    <script type="application/json" id="mcp-sidebar-active-ids">@json($sidebarActiveIds ?? [])</script>
    @persist('mcp-sidebar')
        @include('includes.menu')
    @endpersist
    @include('includes.ask-ai-panel')
    <div class="wrapper d-flex flex-column min-vh-100">
        @unless(app()->environment('production'))
            <div class="env-banner text-center fw-semibold py-1 small">This is a testing environment</div>
        @endunless
        @include('includes.topbar')

        <main class="mcp-page flex-grow-1" role="main">
            @if(isset($slot))
                {{ $slot }}
            @endif
            @yield('content')
        </main>
        <footer class="footer mt-4">
            <div>&copy; Bloomfield Print Works</div>
        </footer>
    </div>


    <!-- Volt JS -->
    <script defer src="{{ asset('js/coreui.bundle.min.js') }}"></script>
    <script defer src="{{ asset('js/mcp-sidebar.js') }}"></script>
    <script defer src="{{ asset('js/vendor/marked.min.js') }}"></script>
    <script defer src="{{ asset('js/vendor/purify.min.js') }}"></script>
    <script defer src="{{ asset('js/mcp-ai-core.js') }}"></script>
    <script defer src="{{ asset('js/mcp-page-context.js') }}"></script>
    <script defer src="{{ asset('js/mcp-ask-ai-sidebar.js') }}"></script>
    {{-- The panel is the same in every mode. v2 changes the transport and adds
         opt-in modules on top; it never replaces the panel. --}}
    <script defer src="{{ asset('js/mcp-ask-ai-persist.js') }}"></script>
    <script defer src="{{ asset('js/mcp-ask-ai-stream-ui.js') }}"></script>
    <script defer src="{{ asset('js/mcp-ask-stream-client.js') }}"></script>
    <script defer src="{{ asset('js/mcp-ask-ai-feedback.js') }}"></script>
    <script defer src="{{ asset('js/mcp-dropdown.js') }}"></script>
    <script defer src="{{ asset('js/mcp-ask-ai-composer.js') }}"></script>
    <script defer src="{{ asset('js/mcp-ask-ai-usage-meter.js') }}"></script>
    @if($askV2Enabled)
        @vite(['resources/js/ask_ai_v2/restore_client.js'])
    @endif
    <script defer src="{{ asset('js/mcp-ask-ai.js') }}"></script>
    @if($askV2Enabled)
        @vite(['resources/js/ask_ai_v2/index.js'])
    @endif
    <script defer src="{{ asset('js/mcp-table-scroll.js') }}"></script>

    <script defer src="https://code.jquery.com/jquery-3.6.1.slim.min.js"
        integrity="sha256-w8CvhFs7iHNVUtnSP0YKEg00p9Ih13rlL9zGqvLdePA=" crossorigin="anonymous"></script>
    @livewireScripts

    <script>
        // CoreUI v4 ships no jQuery interface, so the Livewire close-modal
        // listeners' $('#modal-x').modal('hide') calls would throw (or, when
        // npm Bootstrap's interface was loaded, silently act on the wrong
        // Modal instance). Map $.fn.modal onto CoreUI instances — the same
        // instances the data-coreui-toggle data-API opens. Registered at
        // DOMContentLoaded, after the deferred jQuery + CoreUI bundles run.
        document.addEventListener('DOMContentLoaded', function () {
            if (window.jQuery && window.coreui && !window.jQuery.fn.modal) {
                window.jQuery.fn.modal = function (action) {
                    return this.each(function () {
                        var instance = window.coreui.Modal.getOrCreateInstance(this);
                        if (typeof action === 'string' && typeof instance[action] === 'function') {
                            instance[action]();
                        }
                    });
                };
            }
        });
    </script>

    @stack('scripts')
    @vite(['resources/js/app.js'])
    <!-- Alert -->
    <script src="{{ asset('vendor/sweetalert2/dist/sweetalert2.min.js')}}"></script>

    <script>
        //Sweet alert for livewire
        const toastMixin = Swal.mixin({
            toast: true,
            animation: false,
            position: 'top-end',
            showConfirmButton: false,
            timer: 4000,
            timerProgressBar: true,
            didOpen: (toast) => {
                toast.addEventListener('mouseenter', Swal.stopTimer)
                toast.addEventListener('mouseleave', Swal.resumeTimer)
            }
        });

        window.addEventListener('swal:info', event => {
            toastMixin.fire({
                animation: true,
                icon: event.detail.title,
                title: '<b style="font-size:13pt!important; padding-left:30px">' + event.detail.text + '</b>'
            });
        })

        // Notification toast popup with ding sound
        var _notifCtx = null;
        window.addEventListener('notification:toast', function(event) {
            try {
                if (!_notifCtx) _notifCtx = new (window.AudioContext || window.webkitAudioContext)();
                var osc = _notifCtx.createOscillator();
                var gain = _notifCtx.createGain();
                osc.connect(gain);
                gain.connect(_notifCtx.destination);
                osc.type = 'sine';
                osc.frequency.value = 880;
                gain.gain.value = 0.3;
                osc.start();
                gain.gain.exponentialRampToValueAtTime(0.001, _notifCtx.currentTime + 0.5);
                osc.stop(_notifCtx.currentTime + 0.5);
            } catch(e) {}

            // Show toast
            var notifUrl = event.detail.url || null;
            var actorHtml = '';
            if (event.detail.actor && event.detail.actor.name) {
                var safeName = String(event.detail.actor.name)
                    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
                actorHtml = '<div style="font-size:9pt; color:#999; padding: 4px 8px 0; text-align:left;">by ' + safeName + '</div>';
            }
            toastMixin.fire({
                animation: true,
                icon: 'info',
                timer: 15000,
                width: 420,
                showCloseButton: true,
                title: '<b style="font-size:11pt!important; padding-left:30px">' + event.detail.title + '</b>',
                html: '<div style="font-size:10pt; padding: 4px 8px 0; text-align:left; word-break:break-word; white-space:normal; line-height:1.4; max-height:120px; overflow-y:auto;">' + event.detail.message + '</div>' + actorHtml,
                didOpen: function(toast) {
                    toast.addEventListener('mouseenter', Swal.stopTimer);
                    toast.addEventListener('mouseleave', Swal.resumeTimer);
                    if (notifUrl) {
                        toast.style.cursor = 'pointer';
                        toast.style.transition = 'filter 0.15s ease';
                        toast.addEventListener('mouseenter', function() { toast.style.filter = 'brightness(0.95)'; });
                        toast.addEventListener('mouseleave', function() { toast.style.filter = ''; });
                        toast.addEventListener('click', function() { window.location.href = notifUrl; });
                    }
                }
            });
        });

        // Tab title unread count
        window.addEventListener('notification:count', function(event) {
            var count = event.detail.count;
            var base = document.title.replace(/^\(\d+\)\s*/, '');
            document.title = count > 0 ? '(' + count + ') ' + base : base;
        });

    </script>

    @if (session()->has('swal:info'))
        <script>
            toastMixin.fire({
                animation: true,
                icon: 'success',
                title: '<b style="font-size:13pt!important; padding-left:30px">{{ session('swal:info') }}</b>'
            });
        </script>
    @endif

    <script>
        function PrintElem(elem, header = true) {
            Popup($(elem).html(), header);
        }

        function Popup(data, header) {
            //Print date
            var today = new Date();
            var dd = today.getDate();
            var mm = today.getMonth() + 1; //January is 0!
            var yyyy = today.getFullYear();
            var h = today.getHours();
            var m = today.getMinutes();
            var s = today.getSeconds();
            if (h < 10) {
                h = '0' + h;
            }
            if (h < 10) {
                m = '0' + m;
            }
            if (h < 10) {
                s = '0' + s;
            }
            var time = h + ':' + m + ':' + s;
            if (dd < 10) {
                dd = '0' + dd
            }
            if (mm < 10) {
                mm = '0' + mm
            }
            today = dd + '/' + mm + '/' + yyyy + ' Time ' + time;
            var mywindow = window.open('', 'Report Print', 'height=800,width=900');
            mywindow.document.write('<html><title>Report Printing</title>');
            mywindow.document.write('<link rel="stylesheet" id="theme-style" href="{{asset('css/simple.css')}}">');
            if (header) {
                mywindow.document.write('<img src="{{asset('img/logo/logo_mcp.jpg')}}" height="82" style="display: block;margin-left: auto;margin-right: auto;">');
            }
            mywindow.document.write('<style>' + '.stat_width{width: 20%}' +
                'table {font-family: "tahoma",arial, sans-serif;border-collapse: collapse;width: 100%;}td, th { border: 1px solid #c3c3c3;text-align: left;padding: 4px;}tr:nth-child(even) {background-color: #ffffff;}</style>');
            mywindow.document.write('<body style="background: white;margin-bottom: 30px">');
            mywindow.document.write(data);
            mywindow.document.write('Printed at: ' + today);
            if (header) {
                mywindow.document.write('<div style="margin-top: 200px;position: fixed;bottom:0;width: 100%;">' +
                    '<hr/>' +
                    '<small>Bloomfield Print Works</small> ' +
                    '</div>');
            }

            mywindow.document.write('</body></html>');
            mywindow.document.close(); // necessary for IE >= 10
            mywindow.focus(); // necessary for IE >= 10
            setTimeout(function () { mywindow.print(); }, 1000);
            // mywindow.close();
            return true;
        }
    </script>

    <script>
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/service-worker.js')
                .catch(function (err) { console.warn('SW registration failed:', err); });
        }
    </script>
</body>

</html>
