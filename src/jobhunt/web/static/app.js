// A plain form's button says what it is doing and cannot be pressed twice.
// HTMX forms use hx-indicator instead; they are skipped here.
document.addEventListener('submit', function (event) {
  if (event.target.hasAttribute('hx-post')) return;
  var button = event.submitter || event.target.querySelector('button[type=submit]');
  if (!button) return;
  button.textContent = button.dataset.busy || 'Working…';
  setTimeout(function () { button.disabled = true; }, 0);
});
