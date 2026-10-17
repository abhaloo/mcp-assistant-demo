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
        Schema::table('accounts', function (Blueprint $table) {
            $table->string('account_name')->after('id')->nullable();
            $table->string('account_number')->after('account_name')->nullable();
            $table->string('swift_code')->after('account_number')->nullable();
            $table->string('bank_name')->after('swift_code')->nullable();
        });
    }

    /**
     * Reverse the migrations.
     *
     * @return void
     */
    public function down()
    {
        Schema::table('accounts', function (Blueprint $table) {
            $table->dropColumn('account_name', 'account_number','swift_code', 'bank_name');
        });
    }
};
