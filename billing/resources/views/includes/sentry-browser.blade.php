@php($sentryJsDsn = config('services.sentry.js_dsn'))
@if ($sentryJsDsn)
    {{-- Loaded early and blocking so it initialises before other scripts and can catch
         their errors. Renders only when SENTRY_JS_DSN is configured. --}}
    <script src="https://browser.sentry-cdn.com/10.57.0/bundle.min.js" crossorigin="anonymous"></script>
    <script>
        if (window.Sentry) {
            Sentry.init({
                dsn: @json($sentryJsDsn),
                environment: @json(config('services.sentry.environment')),
                @if (config('services.sentry.release'))
                release: @json(config('services.sentry.release')),
                @endif
                // Performance tracing off to conserve the free-tier quota; errors still captured.
                tracesSampleRate: 0,
            });
            @auth
                Sentry.setUser({
                    id: @json((string) auth()->id()),
                    username: @json(auth()->user()->username),
                });
            @endauth
        }
    </script>
@endif
