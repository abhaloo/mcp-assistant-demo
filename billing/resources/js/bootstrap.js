// Intentionally NOT importing npm Bootstrap's JS here.
// The CoreUI bundle (public/js/coreui.bundle.min.js, loaded in layouts/app.blade.php)
// provides the same components and must own jQuery's $.fn.modal — loading both
// runtimes made npm Bootstrap register $.fn.modal last, so $('#modal').modal('hide')
// acted on a fresh (never-shown) Bootstrap instance and silently no-oped.
// The `bootstrap` npm package stays in package.json: resources/sass/app.scss imports its SCSS.
