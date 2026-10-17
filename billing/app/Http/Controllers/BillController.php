<?php

namespace App\Http\Controllers;

use App\Models\Bill;
use App\Support\UserAgent;
use Illuminate\Http\Request;

class BillController extends Controller
{
    public function index(Request $request)
    {
        $type = $request->get('type');
        if ($type !== null && $type !== 'Invoice') {
            abort(404);
        }
        $type = $type ?? 'Invoice';

        return view('bills.index', compact('type'));
    }

    public function show(Bill $bill, Request $request)
    {
        $this->authorize('view', $bill);

        $fromOrder = $request->query('from_order');

        $agent = UserAgent::fromRequest($request);
        $isMobile = $agent->isMobile() && ! $agent->isTablet();

        $view = $isMobile ? 'bills.show-mobile' : 'bills.show';

        return view($view, compact('bill', 'fromOrder'));
    }
}
