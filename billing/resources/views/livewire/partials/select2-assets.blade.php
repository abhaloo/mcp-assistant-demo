@once
@push('styles')
    <link rel="stylesheet" href="{{ asset('css/select2.css') }}" media="print" onload="this.media='all'">
    <link rel="stylesheet" href="{{ asset('css/select2_theme.css') }}" media="print" onload="this.media='all'">
@endpush
@push('scripts')
    <script defer src="{{ asset('js/select2.js') }}"></script>
@endpush
@endonce
