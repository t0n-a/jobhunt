// A plain form's button says what it is doing and cannot be pressed twice.
// HTMX forms use hx-indicator instead; they are skipped here.
document.addEventListener('submit', function (event) {
  if (event.target.hasAttribute('hx-post')) return;
  var button = event.submitter || event.target.querySelector('button[type=submit]');
  if (!button) return;
  button.textContent = button.dataset.busy || 'Working…';
  setTimeout(function () { button.disabled = true; }, 0);
});

// The transcript grows at the bottom; keep the newest turn in view.
document.addEventListener('htmx:afterSwap', function (event) {
  if (event.target && event.target.id === 'transcript') {
    var last = event.target.lastElementChild;
    if (last && last.scrollIntoView) last.scrollIntoView({ block: 'end', behavior: 'smooth' });
  }
});

// In the counselor composer, Enter sends and Shift+Enter starts a new line.
document.addEventListener('keydown', function (event) {
  var box = event.target;
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;
  if (!box.matches || !box.matches('form.composer textarea')) return;
  event.preventDefault();
  box.form.requestSubmit();
});
