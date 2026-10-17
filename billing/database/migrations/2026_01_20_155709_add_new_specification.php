<?php

use App\Models\Specification;
use App\Models\SpecificationItem;
use Illuminate\Database\Migrations\Migration;

return new class extends Migration
{
    /**
     * Run the migrations.
     *
     * @return void
     */
    public function up()
    {
        // Skip during tests to avoid foreign key issues
        if (app()->runningUnitTests()) {
            return;
        }

        $spec = Specification::firstOrCreate(['name' => 'New']);
        $specId = $spec->id;

        $items = [
            [
                'name' => 'Type of Printing',
                'value' => '["Offset","Digital","Large Format","Outsourced"]',
                'type' => 'Options',
                'columns' => '12',
                'sort_order' => 10,
                'group_name' => null,
            ],
            [
                'name' => 'Machine Engaged',
                'value' => '["SM74-4","SM74-2","Indigo","Ecosolvent 2.2","Ecosolvent 3.2","UV Solvent 2.2","Other"]',
                'type' => 'Options',
                'columns' => '12',
                'sort_order' => 20,
                'group_name' => null,
            ],
            [
                'name' => "No. of UP's",
                'value' => '[]',
                'type' => 'Numbers',
                'columns' => '3',
                'sort_order' => 30,
                'group_name' => null,
            ],
            [
                'name' => 'Final Size of Job',
                'value' => '[]',
                'type' => 'Strings',
                'columns' => '3',
                'sort_order' => 40,
                'group_name' => null,
            ],
            [
                'name' => '1 Side Ptg',
                'value' => '[]',
                'type' => 'Box',
                'columns' => '3',
                'sort_order' => 50,
                'group_name' => null,
            ],
            [
                'name' => '2 Side Duplex Ptg',
                'value' => '[]',
                'type' => 'Box',
                'columns' => '3',
                'sort_order' => 60,
                'group_name' => null,
            ],
            [
                'name' => 'No. of Impression for Inside',
                'value' => '[]',
                'type' => 'Numbers',
                'columns' => '6',
                'sort_order' => 70,
                'group_name' => null,
            ],
            [
                'name' => 'No. of Impression for Cover',
                'value' => '[]',
                'type' => 'Numbers',
                'columns' => '6',
                'sort_order' => 80,
                'group_name' => null,
            ],
            [
                'name' => 'No. of Copies per book',
                'value' => '["1+1","1+2","1+3","1+4","Original Only"]',
                'type' => 'Options',
                'columns' => '12',
                'sort_order' => 110,
                'group_name' => null,
            ],
            [
                'name' => '1st Copy',
                'value' => '[]',
                'type' => 'Strings',
                'columns' => '3',
                'sort_order' => 120,
                'group_name' => null,
            ],
            [
                'name' => '2nd Copy',
                'value' => '[]',
                'type' => 'Strings',
                'columns' => '3',
                'sort_order' => 130,
                'group_name' => null,
            ],
            [
                'name' => '3rd Copy',
                'value' => '[]',
                'type' => 'Strings',
                'columns' => '3',
                'sort_order' => 140,
                'group_name' => null,
            ],
            [
                'name' => '4th Copy',
                'value' => '[]',
                'type' => 'Strings',
                'columns' => '3',
                'sort_order' => 150,
                'group_name' => null,
            ],
            [
                'name' => 'No. of Plates Used for Inside',
                'value' => '[]',
                'type' => 'Numbers',
                'columns' => '3',
                'group_name' => 'OFFSET/INDIGO',
                'sort_order' => 200,
            ],
            [
                'name' => 'Inside Condition',
                'value' => '["New","Old"]',
                'type' => 'Options',
                'columns' => '3',
                'group_name' => 'OFFSET/INDIGO',
                'sort_order' => 210,
            ],
            [
                'name' => 'No. of Plates Used for Cover',
                'value' => '[]',
                'type' => 'Numbers',
                'columns' => '3',
                'group_name' => 'OFFSET/INDIGO',
                'sort_order' => 220,
            ],
            [
                'name' => 'Cover Condition',
                'value' => '["New","Old"]',
                'type' => 'Options',
                'columns' => '3',
                'group_name' => 'OFFSET/INDIGO',
                'sort_order' => 230,
            ],
            [
                'name' => 'Type of Paper (Inside)',
                'value' => '[]',
                'type' => 'Hard List',
                'columns' => '6',
                'group_name' => 'OFFSET/INDIGO',
                'sort_order' => 240,
            ],
            [
                'name' => 'Type of Paper (Cover)',
                'value' => '[]',
                'type' => 'Hard List',
                'columns' => '6',
                'group_name' => 'OFFSET/INDIGO',
                'sort_order' => 250,
            ],
            [
                'name' => 'No. of sheets (Inside)',
                'value' => '[]',
                'type' => 'Numbers',
                'columns' => '6',
                'group_name' => 'OFFSET/INDIGO',
                'sort_order' => 260,
            ],
            [
                'name' => 'No. of sheets (Cover)',
                'value' => '[]',
                'type' => 'Numbers',
                'columns' => '6',
                'group_name' => 'OFFSET/INDIGO',
                'sort_order' => 270,
            ],
            [
                'name' => 'Ink Colour (Inside)',
                'value' => '[]',
                'type' => 'Strings',
                'columns' => '6',
                'group_name' => 'OFFSET/INDIGO',
                'sort_order' => 280,
            ],
            [
                'name' => 'Ink Colour (Cover)',
                'value' => '[]',
                'type' => 'Strings',
                'columns' => '6',
                'group_name' => 'OFFSET/INDIGO',
                'sort_order' => 290,
            ],
            [
                'name' => 'Size of paper to be cut for Inside',
                'value' => '[]',
                'type' => 'Strings',
                'columns' => '6',
                'group_name' => 'OFFSET/INDIGO',
                'sort_order' => 300,
            ],
            [
                'name' => 'Size of sheets to be cut (Cover)',
                'value' => '[]',
                'type' => 'Strings',
                'columns' => '6',
                'group_name' => 'OFFSET/INDIGO',
                'sort_order' => 310,
            ],
            [
                'name' => 'Type of Material used',
                'value' => '[]',
                'type' => 'Hard List',
                'columns' => '6',
                'group_name' => 'LARGE FORMAT',
                'sort_order' => 400,
            ],
            [
                'name' => 'Total Material used',
                'value' => '[]',
                'type' => 'Strings',
                'columns' => '6',
                'group_name' => 'LARGE FORMAT',
                'sort_order' => 410,
            ],
            [
                'name' => 'Total Glue used',
                'value' => '[]',
                'type' => 'Strings',
                'columns' => '3',
                'group_name' => 'LARGE FORMAT',
                'sort_order' => 420,
            ],
            [
                'name' => 'Finishing Instruction',
                'value' => '[]',
                'type' => 'Strings',
                'columns' => '12',
                'group_name' => 'FINISHING',
                'sort_order' => 430,
            ],
            [
                'name' => 'Indigo Counter Start No. for Inside Page',
                'value' => '[]',
                'type' => 'Numbers',
                'columns' => '4',
                'group_name' => 'OFFSET/INDIGO',
                'sort_order' => 500,
            ],
            [
                'name' => 'Indigo Counter Start No. for Cover Page',
                'value' => '[]',
                'type' => 'Numbers',
                'columns' => '4',
                'group_name' => 'OFFSET/INDIGO',
                'sort_order' => 510,
            ],
            [
                'name' => 'Total Clicks (Inside)',
                'value' => '[]',
                'type' => 'Numbers',
                'columns' => '4',
                'group_name' => 'OFFSET/INDIGO',
                'sort_order' => 520,
            ],
            [
                'name' => 'Indigo Counter End No. for Inside Page',
                'value' => '[]',
                'type' => 'Numbers',
                'columns' => '4',
                'group_name' => 'OFFSET/INDIGO',
                'sort_order' => 530,
            ],
            [
                'name' => 'Indigo Counter End No. for Cover Page',
                'value' => '[]',
                'type' => 'Numbers',
                'columns' => '4',
                'group_name' => 'OFFSET/INDIGO',
                'sort_order' => 540,
            ],
            [
                'name' => 'Total Clicks (Cover)',
                'value' => '[]',
                'type' => 'Numbers',
                'columns' => '4',
                'group_name' => 'OFFSET/INDIGO',
                'sort_order' => 550,
            ],
            [
                'name' => 'Coating',
                'value' => '["Yes","Glossy","Matt"]',
                'type' => 'Options',
                'columns' => '6',
                'group_name' => 'OFFSET/INDIGO',
                'sort_order' => 600,
            ],
            [
                'name' => 'Machine Engaged (Coating)',
                'value' => '["SM74-4","Coating"]',
                'type' => 'Options',
                'columns' => '6',
                'group_name' => 'OFFSET/INDIGO',
                'sort_order' => 620,
            ],
            [
                'name' => 'Numbering',
                'value' => '["Yes","Auto Print","Indigo"]',
                'type' => 'Options',
                'columns' => '12',
                'group_name' => 'FINISHING',
                'sort_order' => 640,
            ],
            [
                'name' => 'Starting Number',
                'value' => '[]',
                'type' => 'Strings',
                'columns' => '4',
                'group_name' => 'LARGE FORMAT',
                'sort_order' => 670,
            ],
            [
                'name' => 'End Number',
                'value' => '[]',
                'type' => 'Strings',
                'columns' => '4',
                'group_name' => 'LARGE FORMAT',
                'sort_order' => 680,
            ],
            [
                'name' => 'Lamination',
                'value' => '["Yes","Glossy","Matt","1 side","2 side"]',
                'type' => 'Options',
                'columns' => '6',
                'group_name' => 'FINISHING',
                'sort_order' => 690,
            ],
            [
                'name' => 'Type of Lamination',
                'value' => '["Cold","Document"]',
                'type' => 'Options',
                'columns' => '6',
                'group_name' => 'FINISHING',
                'sort_order' => 720,
            ],
            [
                'name' => 'Size',
                'value' => '["A2","A3","A4","A5"]',
                'type' => 'Options',
                'columns' => '6',
                'group_name' => 'FINISHING',
                'sort_order' => 730,
            ],
            [
                'name' => 'Perforation',
                'value' => '["Yes"]',
                'type' => 'Options',
                'columns' => '6',
                'group_name' => 'FINISHING',
                'sort_order' => 740,
            ],
            [
                'name' => 'Type of Perforation',
                'value' => '["Table Top"]',
                'type' => 'Options',
                'columns' => '6',
                'group_name' => 'FINISHING',
                'sort_order' => 750,
            ],
            [
                'name' => 'Folding',
                'value' => '["Yes"]',
                'type' => 'Options',
                'columns' => '6',
                'group_name' => 'FINISHING',
                'sort_order' => 780,
            ],
            [
                'name' => 'Punching / Dye Cutting / Creasing',
                'value' => '["Yes"]',
                'type' => 'Options',
                'columns' => '6',
                'group_name' => 'FINISHING',
                'sort_order' => 790,
            ],
            [
                'name' => 'Binding',
                'value' => '["Yes"]',
                'type' => 'Options',
                'columns' => '6',
                'group_name' => 'FINISHING',
                'sort_order' => 800,
            ],
            [
                'name' => 'Saddle Stitch',
                'value' => '["Yes"]',
                'type' => 'Options',
                'columns' => '6',
                'group_name' => 'FINISHING',
                'sort_order' => 810,
            ],
            [
                'name' => 'Perfect Binding',
                'value' => '["Yes"]',
                'type' => 'Options',
                'columns' => '6',
                'group_name' => 'FINISHING',
                'sort_order' => 820,
            ],
            [
                'name' => 'Top Glue',
                'value' => '["Yes"]',
                'type' => 'Options',
                'columns' => '6',
                'group_name' => 'FINISHING',
                'sort_order' => 830,
            ],
            [
                'name' => 'Extras',
                'value' => '[]',
                'type' => 'Description',
                'columns' => '6',
                'group_name' => 'FINISHING',
                'sort_order' => 840,
            ],
            [
                'name' => 'Delivery Instructions & Extra Remarks',
                'value' => '[]',
                'type' => 'Description',
                'columns' => '12',
                'sort_order' => 900,
                'group_name' => null,
            ],
        ];

        foreach ($items as $item) {
            SpecificationItem::updateOrCreate(
                [
                    'specification_id' => $specId,
                    'name' => $item['name'],
                ],
                [
                    'value' => $item['value'],
                    'type' => $item['type'],
                    'columns' => $item['columns'],
                    'group_name' => $item['group_name'],
                    'sort_order' => $item['sort_order'],
                ]
            );
        }
    }

    /**
     * Reverse the migrations.
     *
     * @return void
     */
    public function down()
    {
        $spec = Specification::where('name', 'New')->first();
        if ($spec) {
            $spec->items()->delete();
            $spec->delete();
        }
    }
};
