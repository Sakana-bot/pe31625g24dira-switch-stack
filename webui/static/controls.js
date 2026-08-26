'use strict';

const selectControls = new WeakMap();
let openControl = null;
let nextControlId = 0;

function optionLabel(option) {
  return option ? option.textContent.trim() : '请选择';
}

function selectLabel(select) {
  if (select.getAttribute('aria-label')) return select.getAttribute('aria-label');
  const label = select.closest('label');
  if (label) {
    const copy = label.cloneNode(true);
    copy.querySelectorAll('select,input,button').forEach((control) => control.remove());
    const text = copy.textContent.trim();
    if (text) return text;
  }
  const lane = select.closest('.lane-row')?.querySelector(':scope > span');
  if (lane) return `${lane.textContent.trim()}速率`;
  const port = select.closest('tr')?.querySelector('.port-cell strong');
  if (port) return `${port.textContent.trim()} PVID`;
  return '选择';
}

function closeControl(control, restoreFocus = false) {
  if (!control || control.menu.hidden) return;
  control.menu.hidden = true;
  control.trigger.setAttribute('aria-expanded', 'false');
  if (openControl === control) openControl = null;
  if (restoreFocus && control.trigger.isConnected) control.trigger.focus();
}

export function closeCustomSelects() {
  closeControl(openControl);
}

export function destroySelects(root) {
  root.querySelectorAll('select.native-control-proxy').forEach((select) => {
    const control = selectControls.get(select);
    if (!control) return;
    if (openControl === control) closeControl(control);
    control.observer.disconnect();
    control.menu.remove();
    selectControls.delete(select);
  });
}

function positionMenu(control) {
  const { trigger, menu } = control;
  const rect = trigger.getBoundingClientRect();
  const margin = 8;
  const availableBelow = window.innerHeight - rect.bottom - margin;
  const availableAbove = rect.top - margin;
  const maxHeight = Math.max(120, Math.min(320, Math.max(availableBelow, availableAbove)));
  const width = Math.max(rect.width, 120);
  menu.style.width = `${width}px`;
  menu.style.maxHeight = `${maxHeight}px`;
  menu.style.left = `${Math.min(rect.left, window.innerWidth - width - margin)}px`;
  if (availableBelow >= Math.min(menu.scrollHeight, maxHeight) || availableBelow >= availableAbove) {
    menu.style.top = `${rect.bottom + 5}px`;
    menu.style.bottom = 'auto';
  } else {
    menu.style.top = 'auto';
    menu.style.bottom = `${window.innerHeight - rect.top + 5}px`;
  }
}

function focusOption(control, direction = 0) {
  const options = Array.from(control.menu.querySelectorAll('.custom-select-option:not(:disabled)'));
  if (!options.length) return;
  const focused = document.activeElement;
  let index = options.indexOf(focused);
  if (index < 0) index = options.findIndex((option) => option.getAttribute('aria-selected') === 'true');
  index = Math.max(0, index);
  if (direction) index = (index + direction + options.length) % options.length;
  options[index].focus();
  options[index].scrollIntoView({ block: 'nearest' });
}

function openSelect(control) {
  if (control.select.disabled) return;
  if (openControl && openControl !== control) closeControl(openControl);
  control.menu.hidden = false;
  control.trigger.setAttribute('aria-expanded', 'true');
  openControl = control;
  positionMenu(control);
  window.requestAnimationFrame(() => focusOption(control));
}

function chooseOption(control, option) {
  if (option.disabled) return;
  const changed = control.select.value !== option.value;
  control.select.value = option.value;
  syncSelect(control.select);
  closeControl(control, true);
  if (changed) control.select.dispatchEvent(new Event('change', { bubbles: true }));
}

function rebuildMenu(control) {
  const { select, trigger, value, menu } = control;
  const selected = select.selectedOptions[0] || select.options[0];
  value.textContent = optionLabel(selected);
  trigger.disabled = select.disabled;
  trigger.setAttribute('aria-disabled', String(select.disabled));
  menu.replaceChildren();
  Array.from(select.options).forEach((option, index) => {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'custom-select-option';
    item.id = `${control.id}-option-${index}`;
    item.role = 'option';
    item.dataset.value = option.value;
    item.textContent = optionLabel(option);
    item.disabled = option.disabled;
    item.setAttribute('aria-selected', String(option === selected));
    item.addEventListener('click', () => chooseOption(control, option));
    item.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        focusOption(control, event.key === 'ArrowDown' ? 1 : -1);
      } else if (event.key === 'Home' || event.key === 'End') {
        event.preventDefault();
        const items = Array.from(menu.querySelectorAll('.custom-select-option:not(:disabled)'));
        (event.key === 'Home' ? items[0] : items.at(-1))?.focus();
      } else if (event.key === 'Escape') {
        event.preventDefault();
        closeControl(control, true);
      } else if (event.key === 'Tab') {
        closeControl(control);
      }
    });
    menu.append(item);
  });
}

export function syncSelect(select) {
  const control = selectControls.get(select);
  if (!control) return;
  rebuildMenu(control);
  if (!control.menu.hidden) positionMenu(control);
}

export function enhanceSelect(select) {
  if (!select || selectControls.has(select) || select.classList.contains('native-control-proxy')) return select;
  const accessibleLabel = selectLabel(select);
  nextControlId += 1;
  const id = `custom-select-${nextControlId}`;
  const wrapper = document.createElement('div');
  wrapper.className = 'custom-select';
  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.className = 'custom-select-trigger';
  trigger.id = `${id}-trigger`;
  trigger.setAttribute('role', 'combobox');
  trigger.setAttribute('aria-haspopup', 'listbox');
  trigger.setAttribute('aria-label', accessibleLabel);
  trigger.setAttribute('aria-controls', `${id}-menu`);
  trigger.setAttribute('aria-expanded', 'false');
  const value = document.createElement('span'); value.className = 'custom-select-value';
  const chevron = document.createElement('span'); chevron.className = 'custom-select-chevron'; chevron.setAttribute('aria-hidden', 'true');
  trigger.append(value, chevron);
  const menu = document.createElement('div');
  menu.id = `${id}-menu`;
  menu.className = 'custom-select-menu';
  menu.setAttribute('role', 'listbox');
  menu.setAttribute('aria-labelledby', trigger.id);
  menu.hidden = true;

  select.parentNode.insertBefore(wrapper, select);
  wrapper.append(select, trigger);
  document.body.append(menu);
  select.classList.add('native-control-proxy');
  select.hidden = true;
  select.tabIndex = -1;
  select.setAttribute('aria-hidden', 'true');

  const control = { id, select, wrapper, trigger, value, menu, observer: null };
  selectControls.set(select, control);
  trigger.addEventListener('click', () => menu.hidden ? openSelect(control) : closeControl(control));
  trigger.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (menu.hidden) openSelect(control);
      window.requestAnimationFrame(() => focusOption(control, event.key === 'ArrowDown' ? 1 : -1));
    } else if ((event.key === 'Enter' || event.key === ' ') && menu.hidden) {
      event.preventDefault();
      openSelect(control);
    } else if (event.key === 'Escape') {
      closeControl(control, true);
    }
  });
  select.addEventListener('change', () => syncSelect(select));
  control.observer = new MutationObserver(() => syncSelect(select));
  control.observer.observe(select, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'label', 'selected'] });
  rebuildMenu(control);
  return select;
}

export function enhanceSelects(root = document) {
  root.querySelectorAll('select:not(.native-control-proxy)').forEach(enhanceSelect);
}

const numberInputs = new WeakSet();

export function enhanceNumberInput(input) {
  if (!input || input.type !== 'number' || numberInputs.has(input)) return;
  numberInputs.add(input);
  const wrapper = document.createElement('span'); wrapper.className = input.closest('.fan-trigger-grid') ? 'number-stepper fan-number-stepper' : 'number-stepper';
  const decrease = document.createElement('button'); decrease.type = 'button'; decrease.className = 'number-stepper-button number-stepper-decrease'; decrease.textContent = '−'; decrease.setAttribute('aria-label', '减小');
  const increase = document.createElement('button'); increase.type = 'button'; increase.className = 'number-stepper-button number-stepper-increase'; increase.textContent = '+'; increase.setAttribute('aria-label', '增大');
  input.parentNode.insertBefore(wrapper, input);
  wrapper.append(decrease, input, increase);
  const step = (direction) => {
    if (input.disabled) return;
    direction < 0 ? input.stepDown() : input.stepUp();
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  };
  decrease.addEventListener('click', () => step(-1));
  increase.addEventListener('click', () => step(1));
}

export function enhanceNumberInputs(root = document) {
  root.querySelectorAll('input[type="number"]').forEach(enhanceNumberInput);
}

document.addEventListener('pointerdown', (event) => {
  if (openControl && !openControl.menu.contains(event.target) && !openControl.trigger.contains(event.target)) closeControl(openControl);
});
window.addEventListener('resize', closeCustomSelects);
window.addEventListener('scroll', (event) => {
  if (openControl && openControl.menu.contains(event.target)) return;
  closeCustomSelects();
}, true);
