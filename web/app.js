'use strict';

const paths = {
  settings: '<path d="m9 3-.6 3-2.5 1.5L3 7l-1 3 2 2v3l-1.5 2 2 2.5 3-1 2.5 1 1 2.5h3l1-2.5 2.5-1 2.5 1 2-2.5-1.5-2v-3l2-2-1-3-3 .5L16 6l-.6-3z"/><circle cx="12" cy="12" r="3"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5M5.5 7a7 7 0 0 1 12-1L20 12M4 12l2.5 6a7 7 0 0 0 12-1"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  history: '<path d="M3 4v6h6M3 10a9 9 0 1 1 1 8m8-12v6l3 2"/>',
  key: '<circle cx="8" cy="8" r="5"/><path d="m12 12 9 9m-4-4 3-3m-6 0 3-3"/>',
  shield: '<path d="m12 2 8 4v6c0 6-8 10-8 10S4 18 4 12V6z"/><path d="m8 12 3 3 5-6"/>',
  trash: '<path d="M3 6h18M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7"/>'
};
const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const numberFormat = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 1});
const dateFormat = new Intl.DateTimeFormat('ru-RU', {day:'numeric', month:'short'});
const timeFormat = new Intl.DateTimeFormat('ru-RU', {hour:'2-digit', minute:'2-digit'});
const fmt = value => value === null || value === undefined ? '—' : numberFormat.format(value);
const kind = value => ({exam:'Экзамен', credit:'Зачёт', grading_credit:'Дифф. зачёт'}[value] || value || '');
const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.chevron}</svg>`;
const attended = lesson => [true, 1, '1'].includes(lesson.Attended);

function date(value) {
  if (!value) return '';
  const parsed = new Date(typeof value === 'number' ? value * 1000 : value);
  return Number.isNaN(parsed.getTime()) ? '' : dateFormat.format(parsed);
}
function time(value) {
  return value ? timeFormat.format(new Date(value * 1000)) : '—';
}
function hydrateIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach(element => { element.innerHTML = icon(element.dataset.icon); });
}
hydrateIcons();

const tg = window.Telegram?.WebApp;
if (tg?.initData) { tg.ready(); tg.expand(); }
function syncAppearance() {
  const color = document.documentElement.dataset.theme === 'dark' ? '#000000' : '#ffffff';
  if (tg?.initData) {
    tg.setHeaderColor?.(color);
    tg.setBackgroundColor?.(color);
    tg.setBottomBarColor?.(color);
  }
}
window.addEventListener('appearancechange', syncAppearance);
syncAppearance();

const state = {data: null, busy: false, detail: null, dialog: null};
let toastTimer;
let loadRequest = 0;
let detailRequest = 0;

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch('/api/' + path, {
      ...options,
      headers: {'Content-Type':'application/json', ...(tg?.initData ? {Authorization:'tma ' + tg.initData} : {}), ...options.headers},
      cache: 'no-store'
    });
  } catch {
    throw new Error('Нет связи с сервером. Проверь интернет и повтори попытку.');
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : 'Не удалось выполнить действие. Попробуй позже.');
  }
  return response;
}
const json = async (path, options = {}) => (await api(path, options)).json();
const semesterQuery = () => state.data?.semester ? '?semester=' + encodeURIComponent(state.data.semester) : '';

function toast(message) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4500);
}
function showDialog(content, name) {
  state.dialog = name;
  $('#modal-content').innerHTML = content;
  if (!$('#modal').open) $('#modal').showModal();
  $('#modal').scrollTop = 0;
}
function closeDialog() { $('#modal').close(); }

function subjectRow(subject) {
  const meta = [kind(subject.type)];
  if (subject.score === null) meta.push('Пока без баллов');
  else if (subject.available !== null && subject.available !== undefined) meta.push('Доступно ' + fmt(subject.available));
  return `<button class="subject-row" data-subject="${esc(subject.id)}" aria-label="${esc(subject.name)}, ${fmt(subject.score)} баллов, подробнее">
    <span class="subject-info"><span class="subject-name">${esc(subject.name)}</span><span class="subject-meta">${esc(meta.filter(Boolean).join(' · '))}</span></span>
    <span class="subject-score"><strong>${fmt(subject.score)}</strong><span>/ 100</span></span>
    <span class="row-chevron">${icon('chevron')}</span>
  </button>`;
}
function render() {
  const data = state.data;
  if (!data) return;
  $('#demo-banner').hidden = !data.demo;
  $('#error-banner').hidden = !data.error;
  $('#error-banner').textContent = data.error || '';
  if (!data.linked) { $('#content').innerHTML = connectHTML(); return; }
  $('#content').innerHTML = `
    <div class="page-heading"><h1>Баллы</h1><span class="subject-count">${data.subjects.length} предм.</span></div>
    <div class="semester-toolbar">
      <label class="semester-picker"><span class="sr-only">Семестр</span><select id="semester-select" ${state.busy ? 'disabled' : ''}>${data.semesters.map(semester => `<option value="${esc(semester.id)}" ${semester.id === data.semester ? 'selected' : ''}>${esc(semester.title)}</option>`).join('')}</select>${icon('down')}</label>
      <span class="updated">${data.demo ? 'Демо' : 'Проверено ' + time(data.updated)}</span>
      <button class="icon-button refresh-button" data-action="refresh" ${state.busy ? 'disabled' : ''} aria-label="Обновить баллы">${icon('refresh')}</button>
    </div>
    <div class="subject-list" aria-label="Баллы по предметам">${data.subjects.length ? data.subjects.map(subjectRow).join('') : '<div class="empty-state"><h2>Предметов пока нет</h2><p>В выбранном семестре БРС ещё не вернула предметы.</p></div>'}</div>`;
}
async function load(semester, refresh = false) {
  const request = ++loadRequest;
  if (refresh) { state.busy = true; render(); }
  const suffix = semester ? '?semester=' + encodeURIComponent(semester) : '';
  try {
    const result = await json((refresh ? 'refresh' : 'dashboard') + suffix, refresh ? {method:'POST'} : {});
    if (request !== loadRequest) return;
    if (result.demo && state.data?.demo) result.settings = state.data.settings;
    state.data = result;
    if (refresh) toast(result.demo ? 'Демонстрационные баллы' : 'Проверено');
  } catch (error) {
    if (request !== loadRequest) return;
    if (state.data) toast(error.message);
    else $('#content').innerHTML = `<div class="gate"><h1>Открой приложение в Telegram</h1><p>${esc(error.message)}</p><p>В чате с ботом нажми «Моя БРС» рядом с полем ввода.</p><button class="primary-button" data-action="retry">Повторить</button></div>`;
  } finally {
    if (request === loadRequest) { state.busy = false; render(); }
  }
}
function settingsHTML() {
  const data = state.data;
  const linked = !!data?.linked;
  return `<h2 id="modal-title">Настройки</h2>
    <section class="settings-section"><h3>Оформление</h3><div class="appearance-options" role="group" aria-label="Оформление">${[['dark','Тёмная'],['light','Светлая'],['system','Авто']].map(([value, label]) => `<button data-theme="${value}" aria-pressed="${window.brsyAppearance.get() === value}">${label}</button>`).join('')}</div></section>
    ${linked ? `<div class="settings-row"><div><strong>Уведомления</strong><p>Изменения баллов в Telegram</p></div><button class="toggle" role="switch" aria-label="Уведомления в Telegram" aria-checked="${data.settings.notifications}" data-action="toggle-notifications"></button></div>
    <div class="settings-links">
      <button data-action="history">${icon('history')}<span>История изменений</span>${icon('chevron')}</button>
      <button data-action="export">${icon('download')}<span>Скачать баллы в CSV</span>${icon('chevron')}</button>
      <button data-action="reconnect">${icon('key')}<span>Заменить токен БРС</span>${icon('chevron')}</button>
      <button data-action="privacy">${icon('shield')}<span>Данные и приватность</span>${icon('chevron')}</button>
      <button data-action="delete-account">${icon('trash')}<span>Удалить мои данные</span>${icon('chevron')}</button>
    </div>` : '<p class="settings-note">Подключи токен БРС, чтобы загрузить баллы.</p>'}
    ${data?.demo ? '<p class="settings-note">Деморежим. Уведомления меняются до перезагрузки страницы.</p>' : ''}`;
}
function showSettings() { detailRequest++; showDialog(settingsHTML(), 'settings'); }
function showHistory() {
  const events = state.data?.history || [];
  showDialog(`<h2 id="modal-title">Изменения баллов</h2>${events.length ? `<div class="history-list">${events.map(event => `<div class="history-row"><div><strong>${esc(event.subject)}</strong><time>${date(event.created)}, ${time(event.created)}</time></div><span>${fmt(event.old)} <span class="history-arrow">→</span> ${fmt(event.new)}</span></div>`).join('')}</div>` : '<div class="empty-state"><p>Изменений пока нет. История ведётся с момента подключения.</p></div>'}`, 'history');
}
async function openSubject(id) {
  const request = ++detailRequest;
  showDialog('<h2 id="modal-title">Загрузка предмета…</h2>', 'subject');
  try {
    const detail = await json('subjects/' + encodeURIComponent(id) + semesterQuery());
    if (request !== detailRequest || !$('#modal').open || state.dialog !== 'subject') return;
    state.detail = detail;
    showDialog(detailHTML(detail), 'subject');
  } catch (error) {
    if (request === detailRequest && $('#modal').open) showDialog(`<h2 id="modal-title">Не удалось загрузить</h2><p>${esc(error.message)}</p><button class="primary-button" data-subject="${esc(id)}">Повторить</button>`, 'subject');
  }
}
function detailHTML(detail) {
  const modules = detail.modules || [];
  const lessons = detail.journal?.lessons || [];
  const count = lessons.filter(attended).length;
  const works = modules.map(module => `<section class="module"><h3>${esc(module.title)}</h3>${module.works.map(work => `<div class="work-row"><span>${esc(work.title)}${work.date ? `<small>${date(work.date)}</small>` : ''}</span><strong>${fmt(work.score)}<span> / ${fmt(work.max)}</span></strong></div>`).join('')}</section>`).join('');
  let journal = detail.journal_error ? `<p>${esc(detail.journal_error)}</p>` : lessons.length ? lessons.map(lesson => `<div class="work-row"><span>${date(lesson.LessonDate)}<small>${esc(lesson.LessonType)}</small></span><span class="attendance-label">${attended(lesson) ? 'Посещено' : 'Не отмечено'}</span></div>`).join('') : '<p>В журнале пока нет занятий.</p>';
  if (detail.journal?.gym) journal += `<p class="detail-note">Физкультура: учтено ${esc(detail.journal.gym.TotalAttendance ?? '—')} посещений; за текущий семестр — ${esc(detail.journal.gym.SemesterCount ?? '—')}.</p>`;
  return `<h2 id="modal-title">${esc(detail.name)}</h2><p class="detail-meta">${esc([kind(detail.type), detail.teacher].filter(Boolean).join(' · '))}</p>
    <div class="detail-score"><strong>${fmt(detail.score)}<span> / 100</span></strong><span>Доступно сейчас ${fmt(detail.available)}</span></div>
    ${works || '<p class="detail-note">Учебная карта пока пуста.</p>'}
    ${detail.extra ? `<p class="detail-note">Дополнительные баллы: ${fmt(detail.extra)}</p>` : ''}
    <details class="journal"><summary><span>Посещаемость</span><span class="journal-count">${detail.journal ? count + ' / ' + lessons.length : ''}</span>${icon('down')}</summary><div class="journal-body">${journal}</div></details>`;
}
function connectHTML(reconnect = false) {
  return `<div class="gate"><h${reconnect ? '2 id="modal-title"' : '1'}>${reconnect ? 'Замена токена' : 'Подключение к БРС'}</h${reconnect ? '2' : '1'}><p>Для загрузки баллов нужен студенческий токен БРС.</p>
    <ol class="steps"><li>Войди на <a href="https://grade.sfedu.ru" target="_blank" rel="noreferrer">grade.sfedu.ru ↗</a>.</li><li>В том же браузере открой <a href="https://grade.sfedu.ru/student/authtokenget" target="_blank" rel="noreferrer">страницу токена ↗</a>.</li><li>Скопируй токен из 40 символов и вставь ниже.</li></ol>
    <form id="token-form" class="token-form"><label for="token-input">Токен БРС</label><input id="token-input" name="token" type="password" autocomplete="off" spellcheck="false" placeholder="Вставь токен" maxlength="256" required><p id="token-error" class="form-error" role="alert" hidden></p><button class="primary-button" type="submit">Подключить</button></form>
    <p class="detail-note">${reconnect ? 'При замене аккаунта сохранённые баллы и история сбросятся. ' : ''}Пароль от почты не требуется. Токен хранится зашифрованным.</p></div>`;
}
const actions = {
  settings: showSettings,
  'close-modal': closeDialog,
  history: showHistory,
  retry: () => load(),
  refresh: () => { if (!state.busy) return load(state.data?.semester, true); },
  'toggle-notifications': async () => {
    const notifications = !state.data.settings.notifications;
    await json('settings', {method:'PATCH', body:JSON.stringify({notifications})});
    state.data.settings.notifications = notifications;
    showSettings();
  },
  export: async () => {
    const response = await api('export' + semesterQuery());
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'brsy-grades.csv';
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    toast('CSV готов. Если Telegram не скачал файл, отправь /export боту.');
  },
  reconnect: () => {
    if (state.data.demo) { toast('В демо нельзя подключить реальный токен'); return; }
    showDialog(connectHTML(true), 'connect');
  },
  privacy: () => showDialog('<h2 id="modal-title">Данные и приватность</h2><p>На сервере владельца бота хранятся Telegram ID, имя, зашифрованный токен БРС, баллы, история и настройки.</p><p>Токен используется для чтения данных grade.sfedu.ru и не передаётся в Mini App. Пароль от почты ЮФУ не запрашивается.</p><p>Данные можно удалить в настройках или командой /logout. Резервные копии контролирует владелец сервера.</p>', 'privacy'),
  'delete-account': () => {
    if (state.data.demo) { toast('Это демонстрационный профиль'); return; }
    showDialog('<h2 id="modal-title">Удалить данные?</h2><p>Токен БРС, сохранённые баллы и история будут удалены из рабочей базы.</p><div class="form-actions"><button class="secondary-button" data-action="close-modal">Отмена</button><button class="primary-button" data-action="confirm-delete">Удалить</button></div>', 'delete');
  },
  'confirm-delete': async () => {
    await json('account', {method:'DELETE'});
    closeDialog();
    await load();
    toast('Данные удалены');
  }
};

document.addEventListener('click', async event => {
  const button = event.target.closest('button');
  if (!button) return;
  try {
    if (button.dataset.theme) {
      window.brsyAppearance.set(button.dataset.theme);
      if (state.dialog === 'settings') showSettings();
    } else if (button.dataset.subject) await openSubject(button.dataset.subject);
    else if (actions[button.dataset.action]) {
      button.disabled = true;
      try { await actions[button.dataset.action](); }
      finally { button.disabled = false; }
    }
  } catch (error) { toast(error.message); }
});
document.addEventListener('change', async event => {
  if (event.target.id !== 'semester-select') return;
  event.target.disabled = true;
  await load(event.target.value);
});
document.addEventListener('submit', async event => {
  if (event.target.id !== 'token-form') return;
  event.preventDefault();
  const form = event.target;
  const button = form.querySelector('button[type=submit]');
  const input = form.querySelector('input');
  const token = input.value.trim();
  input.value = '';
  button.disabled = true;
  try {
    await json('connect', {method:'POST', body:JSON.stringify({token})});
    closeDialog();
    await load();
    toast('БРС подключена');
  } catch (error) {
    const label = form.querySelector('#token-error');
    label.hidden = false;
    label.textContent = error.message;
  } finally { button.disabled = false; }
});
$('#modal').addEventListener('click', event => {
  if (event.target !== $('#modal')) return;
  const rect = $('#modal').getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeDialog();
});
$('#modal').addEventListener('close', () => { detailRequest++; state.dialog = null; });
load();
