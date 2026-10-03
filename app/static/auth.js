document.querySelectorAll('[data-auth-form]').forEach((form) => {
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const message = document.getElementById('form-message');
    button.disabled = true;
    message.textContent = '';
    const payload = Object.fromEntries(new FormData(form));
    delete payload.csrf_token;
    if (window.grecaptcha) payload.recaptcha_token = window.grecaptcha.getResponse();
    try {
      // Fetch a current token: login/register rotate the anonymous session.
      const tokenResponse = await fetch('/csrf-token', {credentials: 'same-origin', cache: 'no-store'});
      if (!tokenResponse.ok) throw new Error('Could not get a security token. Please reload.');
      const token = (await tokenResponse.json()).csrf_token;
      const response = await fetch(form.action, {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': token},
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.message || 'Please try again.');
      window.location.assign('/dashboard');
    } catch (error) {
      message.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
});
