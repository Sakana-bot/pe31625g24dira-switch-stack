'use strict';

const form = document.querySelector('#setup-form');
const button = document.querySelector('#setup-submit');
const errorBox = document.querySelector('#setup-error');

fetch('/api/identity', { cache: 'no-store', headers: { Accept: 'application/json' } })
  .then((response) => response.ok ? response.json() : null)
  .then((identity) => { if (identity?.model) document.querySelector('#device-model').textContent = identity.model; })
  .catch(() => {});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  errorBox.hidden = true;
  if (!form.checkValidity()) {
    errorBox.textContent = '用户名需为 3–64 位，密码至少 8 位';
    errorBox.hidden = false;
    return;
  }
  if (form.password.value !== form.confirmPassword.value) {
    errorBox.textContent = '两次输入的密码不一致';
    errorBox.hidden = false;
    form.confirmPassword.focus();
    return;
  }
  button.disabled = true;
  button.textContent = '正在创建…';
  try {
    const response = await fetch('/api/setup', {
      method: 'POST',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ username: form.username.value.trim(), password: form.password.value }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    window.location.replace('/login');
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  } finally {
    button.disabled = false;
    button.textContent = '创建账户';
  }
});
