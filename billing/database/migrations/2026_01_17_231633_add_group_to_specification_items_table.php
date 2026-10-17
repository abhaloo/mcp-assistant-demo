<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    /**
     * Run the migrations.
     *
     * @return void
     */
    public function up()
    {
        Schema::table('specification_items', function (Blueprint $table) {
            $table->string('group_name')->nullable()->after('columns');
            $table->integer('sort_order')->default(0)->after('group_name');
        });
    }

    /**
     * Reverse the migrations.
     *
     * @return void
     */
    public function down()
    {
        Schema::table('specification_items', function (Blueprint $table) {
            $table->dropColumn(['group_name', 'sort_order']);
        });
    }
};
