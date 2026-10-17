<?php

namespace App\Http\Controllers;

use Illuminate\Http\Response;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Redis;

class HealthController extends Controller
{
    public function __invoke(): Response
    {
        try {
            DB::select('select 1');
        } catch (\Throwable) {
            return response('database', 503);
        }

        if (config('health.check_redis')) {
            try {
                Redis::connection()->ping();
            } catch (\Throwable) {
                return response('redis', 503);
            }
        }

        return response('ok', 200);
    }
}
