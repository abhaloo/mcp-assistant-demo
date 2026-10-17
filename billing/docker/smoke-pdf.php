<?php

use Illuminate\Contracts\Console\Kernel;

require __DIR__.'/../vendor/autoload.php';

$app = require __DIR__.'/../bootstrap/app.php';
$app->make(Kernel::class)->bootstrap();

$pdf = $app->make('snappy.pdf');
$out = $pdf->getOutputFromHtml('<h1>smoke</h1>');

echo strlen($out);
