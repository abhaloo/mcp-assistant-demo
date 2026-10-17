<?php

namespace App\Exceptions;

use App\Http\Middleware\AttachRequestContext;
use App\Support\ExceptionMessages;
use Illuminate\Auth\Access\AuthorizationException;
use Illuminate\Auth\AuthenticationException;
use Illuminate\Foundation\Exceptions\Handler as ExceptionHandler;
use Illuminate\Http\Exceptions\HttpResponseException;
use Illuminate\Http\Request;
use Illuminate\Session\TokenMismatchException;
use Illuminate\Support\Facades\Log;
use Illuminate\Validation\ValidationException;
use Psr\Log\LogLevel;
use Sentry\Laravel\Integration;
use Sentry\State\Scope;
use Symfony\Component\HttpKernel\Exception\HttpExceptionInterface;
use Throwable;

class Handler extends ExceptionHandler
{
    /**
     * A list of exception types with their corresponding custom log levels.
     *
     * @var array<class-string<Throwable>, LogLevel::*>
     */
    protected $levels = [
        //
    ];

    /**
     * A list of the exception types that are not reported.
     *
     * @var array<int, class-string<Throwable>>
     */
    protected $dontReport = [
        //
    ];

    /**
     * A list of the inputs that are never flashed to the session on validation exceptions.
     *
     * @var array<int, string>
     */
    protected $dontFlash = [
        'current_password',
        'password',
        'password_confirmation',
    ];

    /**
     * Register the exception handling callbacks for the application.
     *
     * @return void
     */
    public function register()
    {
        $this->reportable(function (Throwable $e) {
            Log::shareContext(array_filter([
                'ref_id' => request_ref_id(),
                'user_id' => auth()->id(),
                'url' => request()->fullUrl(),
                'method' => request()->method(),
            ], static fn ($value) => $value !== null && $value !== ''));
        });

        // Report unhandled exceptions to Sentry (a no-op until SENTRY_LARAVEL_DSN is set).
        // Tags the event with the user-facing ref_id and attributes it to the current user,
        // so a "Reference: ABC123" from a user maps straight to the Sentry event.
        $this->reportable(function (Throwable $e) {
            if (! app()->bound('sentry')) {
                return;
            }

            \Sentry\configureScope(function (Scope $scope): void {
                if ($refId = request_ref_id()) {
                    $scope->setTag('ref_id', $refId);
                }

                if ($user = auth()->user()) {
                    $scope->setUser(array_filter([
                        'id' => $user->id,
                        'username' => $user->username ?? null,
                        'email' => $user->email ?? null,
                    ], static fn ($value) => $value !== null && $value !== ''));
                }
            });

            Integration::captureUnhandledException($e);
        });

        $this->renderable(function (Throwable $e, Request $request) {
            if (! $this->shouldRenderFriendlyResponse($e, $request)) {
                return null;
            }

            return $this->renderFriendlyResponse($request);
        });
    }

    /**
     * Render an exception into an HTTP response.
     *
     * Short-circuits CSRF/session-expiry failures to the login screen instead
     * of showing the stock 419 page, since the session is dead either way.
     */
    public function render($request, Throwable $e)
    {
        if ($e instanceof TokenMismatchException) {
            return redirect()->route('login')->withInput($request->except('password'));
        }

        return parent::render($request, $e);
    }

    protected function shouldRenderFriendlyResponse(Throwable $e, Request $request): bool
    {
        if (config('app.debug')) {
            return false;
        }

        if ($e instanceof ValidationException) {
            return false;
        }

        if ($e instanceof AuthenticationException) {
            return false;
        }

        if ($e instanceof AuthorizationException) {
            return false;
        }

        if ($e instanceof TokenMismatchException) {
            return false;
        }

        if ($e instanceof HttpResponseException) {
            return false;
        }

        if ($e instanceof HttpExceptionInterface) {
            return false;
        }

        return true;
    }

    protected function renderFriendlyResponse(Request $request)
    {
        $refId = AttachRequestContext::refId($request) ?? AttachRequestContext::generateRefId();
        $message = ExceptionMessages::FRIENDLY;

        if ($request->headers->has('X-Livewire') || $request->expectsJson() || $request->is('api/*')) {
            return response()->json([
                'message' => $message,
                'ref' => $refId,
            ], 500);
        }

        return redirect()
            ->back()
            ->withInput($request->except($this->dontFlash))
            ->with('swal:info', $message.' Reference: '.$refId);
    }
}
