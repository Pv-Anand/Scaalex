function toggleSidebar() {
  document.querySelector('.sidebar').classList.toggle('open');
  document.getElementById('sidebarBackdrop')?.classList.toggle('show');
}

function csrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.content || '';
}

function showToast(message) {
  let toast = document.querySelector('.copy-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.className = 'copy-toast';
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 1800);
}

function copyText(elId) {
  const el = document.getElementById(elId);
  const text = el.value !== undefined ? el.value : el.textContent;
  navigator.clipboard.writeText(text).then(() => showToast('Copied to clipboard'));
}

function setLoading(btn, loading, loadingLabel) {
  if (!btn) return;
  if (loading) {
    btn.dataset.originalText = btn.innerHTML;
    btn.innerHTML = `<span class="spinner"></span> ${loadingLabel || 'Working…'}`;
    btn.disabled = true;
  } else {
    btn.innerHTML = btn.dataset.originalText || btn.innerHTML;
    btn.disabled = false;
  }
}

/* ---------------- AI extraction ---------------- */
async function runExtraction(conversationId, btn) {
  const transcriptBox = document.getElementById('transcript-editor');
  const notesBox = document.getElementById('raw-notes-view');
  const sourceText = (transcriptBox && transcriptBox.value.trim()) || (notesBox && notesBox.value) || '';

  if (!sourceText.trim()) {
    showToast('Add notes or a transcript first.');
    return;
  }

  setLoading(btn, true, 'Analyzing…');
  try {
    const res = await fetch('/ai/extract', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
      body: JSON.stringify({ conversation_id: conversationId, source_text: sourceText }),
    });
    const data = await res.json();
    setLoading(btn, false);

    if (data.error) {
      showToast(data.error);
      return;
    }
    renderExtraction(conversationId, data);
  } catch (err) {
    setLoading(btn, false);
    showToast('Extraction failed: ' + err.message);
  }
}

function renderExtraction(conversationId, data) {
  const container = document.getElementById('extraction-review');
  container.style.display = 'block';
  window._extractionData = data;

  let html = `<h3 class="section-title">Review AI-Extracted Information</h3>
    <p class="muted small mb-16">Nothing below becomes part of the permanent record until you confirm it. Uncheck anything inaccurate, edit as needed.</p>
    <div class="form-group"><label>Discussion Summary</label>
      <textarea id="ext-summary" rows="3">${escapeHtml(data.summary || '')}</textarea></div>
    <div class="form-group"><label>Important Context</label>
      <textarea id="ext-context" rows="2">${escapeHtml(data.important_context || '')}</textarea></div>`;

  html += `<div class="timeline-section-title">Decisions</div>`;
  if ((data.decisions || []).length === 0) {
    html += `<p class="muted small mb-16">No decisions detected.</p>`;
  }
  (data.decisions || []).forEach((d, i) => {
    html += `<div class="extraction-card">
      <div class="row">
        <input type="checkbox" id="dec-inc-${i}" checked>
        <div style="flex:1">
          <input type="text" id="dec-text-${i}" value="${escapeHtml(d.decision || '')}" class="mb-8">
          <textarea id="dec-ctx-${i}" rows="2" placeholder="Context">${escapeHtml(d.context || '')}</textarea>
          <div class="form-row mt-8">
            <input type="text" id="dec-owner-${i}" placeholder="Owner" value="${escapeHtml(d.owner || '')}">
          </div>
          ${d.needs_confirmation ? '<span class="needs-confirmation-tag small">Needs Confirmation</span>' : ''}
        </div>
      </div>
    </div>`;
  });

  html += `<div class="timeline-section-title">Action Items</div>`;
  if ((data.action_items || []).length === 0) {
    html += `<p class="muted small mb-16">No action items detected.</p>`;
  }
  (data.action_items || []).forEach((a, i) => {
    html += `<div class="extraction-card">
      <div class="row">
        <input type="checkbox" id="act-inc-${i}" checked>
        <div style="flex:1">
          <input type="text" id="act-task-${i}" value="${escapeHtml(a.task || '')}" class="mb-8">
          <div class="form-row">
            <input type="text" id="act-owner-${i}" placeholder="Owner" value="${escapeHtml(a.owner || '')}">
            <input type="date" id="act-due-${i}" value="${a.due_date || ''}">
            <select id="act-priority-${i}">
              ${['High', 'Medium', 'Low'].map(p => `<option ${p === a.priority ? 'selected' : ''}>${p}</option>`).join('')}
            </select>
          </div>
          ${a.needs_confirmation ? '<span class="needs-confirmation-tag small">Needs Confirmation</span>' : ''}
        </div>
      </div>
    </div>`;
  });

  if ((data.open_questions || []).length) {
    html += `<div class="timeline-section-title">Open Questions</div><ul class="bullets">` +
      data.open_questions.map(q => `<li>${escapeHtml(q)}</li>`).join('') + `</ul>`;
  }

  html += `<div class="form-actions">
    <button class="btn" onclick="confirmExtraction(${conversationId}, this)">Confirm &amp; Save to Record</button>
    <button class="btn btn-secondary" onclick="discardExtraction(${conversationId})">Discard</button>
  </div>`;

  container.innerHTML = html;
  container.scrollIntoView({ behavior: 'smooth' });
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str || '';
  return div.innerHTML;
}

async function confirmExtraction(conversationId, btn) {
  const data = window._extractionData;
  const payload = {
    summary: document.getElementById('ext-summary').value,
    important_context: document.getElementById('ext-context').value,
    open_questions: data.open_questions || [],
    decisions: (data.decisions || []).map((d, i) => ({
      include: document.getElementById(`dec-inc-${i}`).checked,
      decision: document.getElementById(`dec-text-${i}`).value,
      context: document.getElementById(`dec-ctx-${i}`).value,
      owner: document.getElementById(`dec-owner-${i}`).value,
      needs_confirmation: d.needs_confirmation,
    })),
    action_items: (data.action_items || []).map((a, i) => ({
      include: document.getElementById(`act-inc-${i}`).checked,
      task: document.getElementById(`act-task-${i}`).value,
      owner: document.getElementById(`act-owner-${i}`).value,
      due_date: document.getElementById(`act-due-${i}`).value || null,
      priority: document.getElementById(`act-priority-${i}`).value,
      needs_confirmation: a.needs_confirmation,
    })),
  };

  setLoading(btn, true, 'Saving…');
  const res = await fetch(`/clients/${window._clientSlug}/conversations/${conversationId}/confirm-extraction`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
    body: JSON.stringify(payload),
  });
  const result = await res.json();
  if (result.redirect) window.location.href = result.redirect;
}

async function discardExtraction(conversationId) {
  await fetch(`/clients/${window._clientSlug}/conversations/${conversationId}/discard-extraction`, {
    method: 'POST',
    headers: { 'X-CSRFToken': csrfToken() },
  });
  document.getElementById('extraction-review').style.display = 'none';
}

/* ---------------- Email draft ---------------- */
async function generateEmailDraft(overviewId, btn) {
  setLoading(btn, true, 'Drafting…');
  try {
    const res = await fetch(`/clients/${window._clientSlug}/ai-summary/${overviewId}/email`, {
      method: 'POST',
      headers: { 'X-CSRFToken': csrfToken() },
    });
    const data = await res.json();
    setLoading(btn, false);
    if (data.error) { showToast(data.error); return; }
    document.getElementById('email-subject').value = data.subject;
    document.getElementById('email-body').value = data.body;
    document.getElementById('email-draft-panel').style.display = 'block';
    document.getElementById('email-draft-panel').scrollIntoView({ behavior: 'smooth' });
  } catch (err) {
    setLoading(btn, false);
    showToast('Email generation failed: ' + err.message);
  }
}

function toggleEditEmail() {
  const subject = document.getElementById('email-subject');
  const body = document.getElementById('email-body');
  const editing = !subject.readOnly;
  subject.readOnly = editing;
  body.readOnly = editing;
}

/* ---------------- Action item row expand/collapse ---------------- */
function toggleActionItemRow(itemId) {
  const editRow = document.getElementById(`action-item-edit-${itemId}`);
  const toggleBtn = document.getElementById(`action-item-toggle-${itemId}`);
  const open = editRow.style.display !== 'none';
  editRow.style.display = open ? 'none' : 'block';
  toggleBtn.classList.toggle('open', !open);
}

function toggleDecisionEdit(decisionId) {
  const el = document.getElementById(`decision-edit-${decisionId}`);
  el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

/* ---------------- Action item inline status ---------------- */
async function updateActionStatus(itemId, status, selectEl) {
  const res = await fetch(`/action-items/${itemId}/status`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'X-Requested-With': 'fetch',
      'X-CSRFToken': csrfToken(),
    },
    body: `status=${encodeURIComponent(status)}`,
  });
  const data = await res.json();
  if (data.ok) {
    showToast('Status updated');
    selectEl.closest('.action-row')?.classList.toggle('is-completed', status === 'Completed');
  }
}

/* ---------------- Notify Client ---------------- */
let notifyDraftData = null;

function ensureNotifyModal() {
  let overlay = document.getElementById('notify-modal-overlay');
  if (overlay) return overlay;
  overlay = document.createElement('div');
  overlay.id = 'notify-modal-overlay';
  overlay.className = 'notify-modal-overlay';
  overlay.innerHTML = `
    <div class="notify-modal">
      <div class="notify-modal-head">
        <div>
          <div class="notify-modal-eyebrow" id="notify-modal-eyebrow"></div>
          <div class="notify-modal-title" id="notify-modal-title"></div>
          <div class="notify-modal-subtitle" id="notify-modal-subtitle"></div>
        </div>
        <button type="button" class="notify-modal-close" onclick="closeNotifyModal()" aria-label="Close">&times;</button>
      </div>
      <div class="notify-modal-body" id="notify-modal-body"></div>
    </div>`;
  overlay.addEventListener('click', (e) => { if (e.target === overlay) closeNotifyModal(); });
  document.body.appendChild(overlay);
  return overlay;
}

function closeNotifyModal() {
  document.getElementById('notify-modal-overlay')?.classList.remove('show');
  notifyDraftData = null;
}

async function openNotifyModal(btn) {
  const { entityType, entityId, state, eyebrow, title, subtitle } = btn.dataset;
  ensureNotifyModal();
  const eyebrowEl = document.getElementById('notify-modal-eyebrow');
  eyebrowEl.textContent = eyebrow;
  eyebrowEl.className = 'notify-modal-eyebrow' + (state === 'completed' ? ' state-completed' : '');
  document.getElementById('notify-modal-title').textContent = title;
  document.getElementById('notify-modal-subtitle').textContent = subtitle;
  document.getElementById('notify-modal-body').innerHTML = '<div class="notify-modal-loading">Drafting your email and WhatsApp message&hellip;</div>';
  document.getElementById('notify-modal-overlay').classList.add('show');

  try {
    const res = await fetch(`/notify/${entityType}/${entityId}`);
    const data = await res.json();
    if (data.error) {
      document.getElementById('notify-modal-body').innerHTML = `<div class="notify-modal-error">${escapeHtml(data.error)}</div>`;
      return;
    }
    notifyDraftData = data;
    renderNotifyTemplates(data);
  } catch (err) {
    document.getElementById('notify-modal-body').innerHTML = `<div class="notify-modal-error">Couldn't generate templates: ${escapeHtml(err.message)}</div>`;
  }
}

function linkifyPortalUrl(text) {
  return text.replace(/((https?:\/\/)?[a-z0-9.:-]+\/client-login\/[a-z0-9-]+(\/[a-z0-9-]+)*\/?)/gi, '<span class="notify-portal-link">$1</span>');
}

const copyIconSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 012-2h10" stroke-linecap="round"/></svg>';

function renderNotifyTemplates(data) {
  document.getElementById('notify-modal-body').innerHTML = `
    <div class="tmpl-block">
      <div class="tmpl-head">
        <div class="tmpl-head-label">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="M2 6l10 7 10-7" stroke-linecap="round" stroke-linejoin="round"/></svg>
          Email
        </div>
      </div>
      <div class="tmpl-subject-row">
        <div class="tmpl-subject-text">${escapeHtml(data.subject)}</div>
        <button type="button" class="copy-btn" onclick="copyNotifyField('subject')">${copyIconSvg} Copy</button>
      </div>
      <div class="tmpl-body-row">
        <div class="tmpl-body-text">${linkifyPortalUrl(escapeHtml(data.email_body))}</div>
        <button type="button" class="copy-btn" onclick="copyNotifyField('email_body')">${copyIconSvg} Copy</button>
      </div>
    </div>
    <div class="tmpl-block">
      <div class="tmpl-head">
        <div class="tmpl-head-label">
          <svg viewBox="0 0 24 24" fill="none"><path fill="#25984a" d="M12 2a10 10 0 00-8.5 15.2L2 22l4.9-1.5A10 10 0 1012 2zm0 18a8 8 0 01-4.1-1.1l-.3-.2-3 .9.9-2.9-.2-.3A8 8 0 1112 20z"/><path fill="#25984a" d="M17 14.3c-.3-.1-1.6-.8-1.9-.9-.2-.1-.4-.1-.6.1-.2.3-.6.9-.8 1-.1.2-.3.2-.5.1-.3-.1-1.1-.4-2.1-1.3-.8-.7-1.3-1.6-1.5-1.8-.1-.2 0-.4.1-.5l.4-.5c.1-.1.1-.3 0-.4-.1-.1-.6-1.4-.8-1.9-.2-.5-.4-.4-.6-.4h-.5c-.2 0-.4.1-.6.3-.2.3-.8.8-.8 1.9s.8 2.2.9 2.4c.1.2 1.6 2.4 3.8 3.4.5.2.9.4 1.3.5.5.1 1 .1 1.4.1.4-.1 1.3-.5 1.5-1s.2-.9.1-1c0-.1-.2-.2-.4-.3z"/></svg>
          WhatsApp
        </div>
        <button type="button" class="copy-btn" onclick="copyNotifyField('whatsapp_body')">${copyIconSvg} Copy</button>
      </div>
      <div class="tmpl-body">${linkifyPortalUrl(escapeHtml(data.whatsapp_body))}</div>
    </div>`;
}

function copyNotifyField(field) {
  if (!notifyDraftData) return;
  navigator.clipboard.writeText(notifyDraftData[field])
    .then(() => showToast('Copied to clipboard'))
    .catch(() => showToast('Could not copy - select and copy manually'));
}


// ---------------------------------------------------------------- delete tasks and decisions
async function postJSON(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
    body: JSON.stringify(body || {}),
  });
  let data = {};
  try { data = await res.json(); } catch (e) { /* fall through */ }
  if (!res.ok || data.ok === false) throw new Error(data.error || 'Something went wrong. Please try again.');
  return data;
}

function showUndoToast(html, onUndo) {
  document.querySelectorAll('.undo-toast').forEach(el => el.remove());
  const el = document.createElement('div');
  el.className = 'undo-toast';
  const msg = document.createElement('span');
  msg.innerHTML = html;
  el.appendChild(msg);
  if (onUndo) {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = 'Undo';
    b.onclick = async () => { b.disabled = true; try { await onUndo(); } catch (e) { showToast(e.message); b.disabled = false; } };
    el.appendChild(b);
  }
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 10000);
}

function removeDeletedRow(kind, id) {
  if (kind === 'action-items') {
    ['action-item-row-', 'action-item-edit-'].forEach(prefix => { const el = document.getElementById(prefix + id); if (el) el.remove(); });
  } else {
    const edit = document.getElementById('decision-edit-' + id);
    const card = edit && (edit.closest('.decision-timeline-item') || edit.closest('.decision-card'));
    if (card) card.remove();
  }
}

async function deleteItems(kind, ids) {
  if (!ids.length) return;
  const closePop = document.querySelector('.confirm-pop');
  if (closePop) closePop.remove();
  let data;
  try { data = await postJSON('/' + kind + '/delete', { ids }); }
  catch (e) { showToast(e.message); return; }
  ids.forEach(id => removeDeletedRow(kind, id));
  if (typeof clearTaskSelection === 'function') clearTaskSelection();
  const noun = kind === 'action-items' ? 'task' : 'decision';
  showUndoToast(`${data.deleted} ${noun}${data.deleted === 1 ? '' : 's'} deleted`, async () => {
    await postJSON('/' + kind + '/restore', { snapshots: data.snapshots });
    location.reload();
  });
}

function confirmDeleteItems(kind, ids, anchor) {
  if (!ids.length) return;
  const old = document.querySelector('.confirm-pop');
  if (old) old.remove();
  const noun = kind === 'action-items' ? 'task' : 'decision';
  const what = ids.length === 1 ? 'this ' + noun : ids.length + ' ' + noun + 's';
  const pop = document.createElement('div');
  pop.className = 'confirm-pop';
  pop.innerHTML = `<div class="confirm-pop-title">Delete ${what}?</div>
    <p>${ids.length === 1 ? 'It is' : 'They are'} removed from the lists. The update ${ids.length === 1 ? 'it' : 'they'} came from is not changed. You can undo for 10 seconds.</p>
    <div class="confirm-pop-actions"><button type="button" class="btn btn-sm btn-danger-solid" data-go>Delete</button><button type="button" class="link-btn small muted" data-cancel>Cancel</button></div>`;
  document.body.appendChild(pop);
  const r = anchor.getBoundingClientRect();
  pop.style.top = (window.scrollY + r.bottom + 8) + 'px';
  pop.style.left = Math.max(12, Math.min(window.scrollX + r.left, window.scrollX + document.documentElement.clientWidth - 300)) + 'px';
  pop.querySelector('[data-cancel]').onclick = () => pop.remove();
  pop.querySelector('[data-go]').onclick = (ev) => { ev.target.disabled = true; deleteItems(kind, ids); };
  setTimeout(() => document.addEventListener('click', function once(e) {
    if (!pop.contains(e.target) && e.target !== anchor && !anchor.contains(e.target)) { pop.remove(); document.removeEventListener('click', once); }
  }), 0);
}

function showTaskDeleteStrip(id, show) {
  const strip = document.getElementById('task-delete-strip-' + id);
  const actions = document.getElementById('task-actions-' + id);
  if (strip) strip.style.display = show ? 'flex' : 'none';
  if (actions) actions.style.display = show ? 'none' : 'flex';
}

function selectedTaskIds() { return [...document.querySelectorAll('.task-select:checked')].map(c => Number(c.value)); }
function onTaskSelect() {
  const n = selectedTaskIds().length;
  const bar = document.getElementById('task-bulkbar');
  if (bar) { bar.style.display = n ? 'flex' : 'none'; document.getElementById('task-bulk-n').textContent = n; }
  document.querySelectorAll('.action-row').forEach(row => {
    const cb = row.querySelector('.task-select');
    row.classList.toggle('selected', !!(cb && cb.checked));
  });
}
function clearTaskSelection() { document.querySelectorAll('.task-select').forEach(c => { c.checked = false; }); onTaskSelect(); }
