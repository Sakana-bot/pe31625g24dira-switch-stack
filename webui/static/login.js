'use strict';

const form = document.querySelector('#login-form');
const button = document.querySelector('#login-submit');
const errorBox = document.querySelector('#login-error');

fetch('/api/identity', { cache: 'no-store', headers: { Accept: 'application/json' } })
  .then((response) => response.ok ? response.json() : null)
  .then((identity) => { if (identity?.model) document.querySelector('#device-model').textContent = identity.model; })
  .catch(() => {});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!form.username.value.trim() || !form.password.value) {
    errorBox.textContent = '请输入用户名和密码';
    errorBox.hidden = false;
    return;
  }
  button.disabled = true;
  button.textContent = '正在登录…';
  errorBox.hidden = true;
  try {
    const response = await fetch('/api/login', {
      method: 'POST',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ username: form.username.value, password: form.password.value }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    window.location.replace('/');
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
    form.password.focus();
    form.password.select();
  } finally {
    button.disabled = false;
    button.textContent = '登录';
  }
});
