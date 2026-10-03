// Pages share the existing JSON API; no second in-memory data store.
async function apiWrite(path, method, payload) {
  const tokenResponse = await fetch('/csrf-token', {credentials: 'same-origin', cache: 'no-store'});
  if (!tokenResponse.ok) throw new Error('Please log in again.');
  const token = (await tokenResponse.json()).csrf_token;
  const response = await fetch(path, {
    method, credentials: 'same-origin',
    headers: {'Content-Type': 'application/json', 'X-CSRFToken': token},
    body: JSON.stringify(payload),
  });
  const result = response.status === 204 ? {} : await response.json();
  if (!response.ok) {
    const error = new Error(result.message || 'Could not save changes.');
    error.result = result;
    throw error;
  }
  return result;
}

function formPayload(form) {
  const payload = {};
  form.querySelectorAll('[name]:not(:disabled)').forEach((field) => {
    if (field.type === 'submit') return;
    if (field.dataset.list !== undefined) {
      payload[field.name] ||= [];
      if (field.checked) payload[field.name].push(field.value);
    } else if (field.type === 'checkbox') {
      payload[field.name] = field.checked;
    } else if (field.type === 'number' || field.dataset.number !== undefined) {
      payload[field.name] = field.value === '' ? null : Number(field.value);
    } else {
      payload[field.name] = field.value;
    }
  });
  (form.dataset.lists || '').split(' ').filter(Boolean).forEach((name) => {
    payload[name] ||= [];
  });
  return payload;
}

document.querySelectorAll('[data-api-form]').forEach((form) => {
  let confirmedPayload = null;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = event.submitter;
    const message = form.querySelector('[data-message]');
    const confirmation = form.querySelector('[data-confirm-duplicate]');
    const payload = formPayload(form);
    if (button === confirmation && confirmedPayload === JSON.stringify(payload)) {
      payload.confirm_duplicate = true;
    }
    if (confirmation) confirmation.hidden = true;
    if (button) button.disabled = true;
    message.textContent = '';
    try {
      const result = await apiWrite(form.action, form.dataset.method || 'POST', payload);
      if (result.accept_path) {
        message.textContent = 'Share this invitation link (no email was sent): ';
        const link = document.createElement('a');
        link.href = result.accept_path;
        link.textContent = new URL(result.accept_path, window.location.origin).href;
        message.appendChild(link);
        form.reset();
      } else if (result.session && form.dataset.sessionRedirect) {
        window.location.assign('/sessions/' + encodeURIComponent(result.session.id));
      } else {
        window.location.assign(form.dataset.redirect || window.location.href);
      }
    } catch (error) {
      message.textContent = error.message;
      if (confirmation && error.result?.error === 'duplicate_confirmation_required') {
        message.textContent += ' Existing players: ' + error.result.details.map((item) => item.name).join(', ');
        confirmedPayload = JSON.stringify(payload);
        confirmation.hidden = false;
      }
    } finally {
      if (button) button.disabled = false;
    }
  });
});

document.querySelectorAll('[data-api-action]').forEach((button) => {
  const originalLabel = button.textContent;
  let awaitingConfirmation = false;
  let cancelButton;
  button.addEventListener('click', async () => {
    const message = document.getElementById('page-message');
    if (button.dataset.confirm && !awaitingConfirmation) {
      awaitingConfirmation = true;
      message.textContent = button.dataset.confirm;
      button.textContent = 'Confirm: ' + originalLabel;
      cancelButton = document.createElement('button');
      cancelButton.type = 'button';
      cancelButton.className = 'btn';
      cancelButton.textContent = 'Cancel confirmation';
      cancelButton.addEventListener('click', () => {
        awaitingConfirmation = false;
        button.textContent = originalLabel;
        message.textContent = '';
        cancelButton.remove();
      });
      button.after(cancelButton);
      return;
    }
    awaitingConfirmation = false;
    cancelButton?.remove();
    button.textContent = originalLabel;
    button.disabled = true;
    try {
      await apiWrite(button.dataset.apiAction, button.dataset.method || 'POST',
        JSON.parse(button.dataset.payload || '{}'));
      window.location.reload();
    } catch (error) {
      message.textContent = error.message;
      button.disabled = false;
    }
  });
});

const search = document.getElementById('player-search');
if (search) search.addEventListener('input', () => {
  document.querySelectorAll('[data-player-name]').forEach((row) => {
    row.hidden = !row.dataset.playerName.toLowerCase().includes(search.value.trim().toLowerCase());
  });
});

document.querySelectorAll('[data-round-form]').forEach((form) => {
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = event.submitter;
    if (!button || button.disabled) return;
    button.disabled = true;
    const assignments = [];
    form.querySelectorAll('[data-court-id]').forEach((court) => {
      assignments.push({
        court_id: court.dataset.courtId,
        team_a_player_ids: Array.from(court.querySelectorAll('[data-team="a"]'), (input) => input.value),
        team_b_player_ids: Array.from(court.querySelectorAll('[data-team="b"]'), (input) => input.value),
      });
    });
    const sitOuts = Array.from(form.querySelectorAll('[data-sit-out]:checked'), (input) => input.value);
    try {
      await apiWrite(form.action, 'PUT', {assignments, sit_out_player_ids: sitOuts});
      window.location.reload();
    } catch (error) {
      form.querySelector('[data-message]').textContent = error.message;
      button.disabled = false;
    }
  });
});
