<?php

use App\Http\Controllers\AiCreditsController;
use App\Http\Controllers\AiSearchController;
use App\Http\Controllers\AskAi\RestoreEvidenceController;
use App\Http\Controllers\Auth\DemoPersonaController;
use App\Http\Controllers\BillController;
use App\Http\Controllers\CustomerController;
use App\Http\Controllers\WorkOrderController;
use Illuminate\Support\Facades\Auth;
use Illuminate\Support\Facades\Route;

/*
|--------------------------------------------------------------------------
| Web Routes
|--------------------------------------------------------------------------
|
| Here is where you can register web routes for your application. These
| routes are loaded by the RouteServiceProvider within a group which
| contains the "web" middleware group. Now create something great!
|
*/

Auth::routes(['register' => false, 'reset' => false, 'confirm' => false, 'verify' => false]);

Route::post('demo/enter/{persona}', [DemoPersonaController::class, 'enter'])
    ->middleware(['guest', 'throttle:10,1'])
    ->name('demo.enter');

Route::get('/', function () {
    if (auth()->check()) {
        return redirect()->route('work-order.index');
    }

    return view('auth.landing');
})->name('home');

Route::middleware('auth')->group(function () {
    // Bills
    Route::get('bills', [BillController::class, 'index'])->name('bill.index')->middleware('permission:view invoice');
    Route::get('bills/show/{bill}', [BillController::class, 'show'])->name('bill.show')->middleware('permission:view invoice');

    // work orders
    Route::get('jobs', [WorkOrderController::class, 'index'])->name('work-order.index')->middleware('permission:view job');
    Route::get('jobs/show/{work_order?}/{call_back?}', [WorkOrderController::class, 'show'])->name('work-order.show');

    // Customers
    Route::view('customers', 'customers.index')->name('customer.index')->middleware('permission:view customer');
    Route::get('customer/show/{customer?}', [CustomerController::class, 'show'])->name('customer.show')->middleware('permission:view customer');

    // AI search (session auth + CSRF)
    Route::middleware(['throttle:ai-api', 'ai.credits'])
        ->post('/ai-search/stream', [AiSearchController::class, 'stream'])
        ->name('ai-search.stream');
    Route::middleware(['throttle:ai-api', 'ai.credits'])
        ->post('/api/ai/ask/v2', [AiSearchController::class, 'streamV2'])
        ->name('ai-search.v2.stream');
    Route::middleware(['throttle:20,1', 'auth'])
        ->post('/ai-search/cancel', [AiSearchController::class, 'cancel'])
        ->name('ai-search.cancel');
    Route::middleware(['auth', 'throttle:20,1'])
        ->post('/ai-search/feedback', [AiSearchController::class, 'feedback'])
        ->name('ai-search.feedback');
    Route::middleware(['auth', 'throttle:20,1'])
        ->post('/ai-search/timing', [AiSearchController::class, 'timing'])
        ->name('ai-search.timing');
    Route::middleware(['auth', 'throttle:20,1'])
        ->get('/api/ai/credits', [AiCreditsController::class, 'show'])
        ->name('ai-search.credits');
    Route::middleware(['auth', 'throttle:60,1'])
        ->post('/ai-search/restore-evidence', [RestoreEvidenceController::class, 'restore'])
        ->name('ai-search.restore-evidence');
});
