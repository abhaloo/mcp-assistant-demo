<?php

namespace App\Http\Livewire;

use Livewire\Component;

class ProfileComponent extends Component
{
    public $current_password;

    public $new_password;

    public $confirm_password = null;

    public function changePassword()
    {
        abort(403);
    }

    public function render()
    {
        return view('livewire.profile-component');
    }
}
