<?php

namespace App\Http\Controllers;

use App\Models\WorkOrder;
use App\Support\UserAgent;
use Illuminate\Http\Request;

class WorkOrderController extends Controller
{
    public function index()
    {
        return view('works.index');
    }

    public function show(Request $request, WorkOrder $workOrder, $call_back = null)
    {
        $this->authorize('view', $workOrder);
        if (! $workOrder->read_status) {
            $workOrder->read_status = true;
            $workOrder->save();
        }
        $workOrder->load(['messages', 'specification', 'materials', 'materials.inventoryItems']);
        $fromOrder = $request->query('from_order');

        // Detect mobile phones (not tablets) for mobile-optimized view
        $agent = UserAgent::fromRequest($request);
        $isMobile = $agent->isMobile() && ! $agent->isTablet();

        if ($isMobile) {
            return view('works.show-mobile', compact('workOrder', 'call_back', 'fromOrder'));
        }

        return view('works.show', compact('workOrder', 'call_back', 'fromOrder'));
    }
}
