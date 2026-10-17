<?php

namespace App\Http\Controllers\Auth;

use App\Http\Controllers\Controller;
use App\Models\User;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Auth;

class DemoPersonaController extends Controller
{
    public function enter(Request $request, string $persona)
    {
        $username = match ($persona) {
            'finance' => 'demo_finance',
            'warehouse' => 'demo_production',
            default => abort(404),
        };

        $user = User::where('username', $username)->first();

        if (! $user) {
            abort(503, 'Demo persona user not found');
        }

        Auth::login($user);
        $request->session()->regenerate();

        return redirect()->route('work-order.index');
    }
}
