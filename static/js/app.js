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

let notifyEntity = { type: null, id: null };

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
    notifyEntity = { type: entityType, id: entityId };
    renderNotifyTemplates(data);
  } catch (err) {
    document.getElementById('notify-modal-body').innerHTML = `<div class="notify-modal-error">Couldn't generate templates: ${escapeHtml(err.message)}</div>`;
  }
}

function linkifyPortalUrl(text) {
  return text.replace(/((https?:\/\/)?[a-z0-9.:-]+\/client-login\/[a-z0-9-]+(\/[a-z0-9-]+)*\/?)/gi, '<span class="notify-portal-link">$1</span>');
}

const copyIconSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 012-2h10" stroke-linecap="round"/></svg>';

let notifyMail = null;
const tickSvg = '<svg viewBox="0 0 12 12" width="11" height="11" fill="none" stroke="#fff" stroke-width="1.8" style="display:block;"><path d="M2.5 6.2l2.4 2.4 4.6-5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const waSvg = '<svg viewBox="0 0 24 24" fill="none"><path fill="#25984a" d="M12 2a10 10 0 00-8.5 15.2L2 22l4.9-1.5A10 10 0 1012 2zm0 18a8 8 0 01-4.1-1.1l-.3-.2-3 .9.9-2.9-.2-.3A8 8 0 1112 20z"/><path fill="#25984a" d="M17 14.3c-.3-.1-1.6-.8-1.9-.9-.2-.1-.4-.1-.6.1-.2.3-.6.9-.8 1-.1.2-.3.2-.5.1-.3-.1-1.1-.4-2.1-1.3-.8-.7-1.3-1.6-1.5-1.8-.1-.2 0-.4.1-.5l.4-.5c.1-.1.1-.3 0-.4-.1-.1-.6-1.4-.8-1.9-.2-.5-.4-.4-.6-.4h-.5c-.2 0-.4.1-.6.3-.2.3-.8.8-.8 1.9s.8 2.2.9 2.4c.1.2 1.6 2.4 3.8 3.4.5.2.9.4 1.3.5.5.1 1 .1 1.4.1.4-.1 1.3-.5 1.5-1s.2-.9.1-1c0-.1-.2-.2-.4-.3z"/></svg>';

function firstName(full) { return (full || '').trim().split(/\s+/)[0] || ''; }
function greetingFor(names) {
  if (!names.length) return 'there';
  if (names.length === 1) return names[0];
  return names.slice(0, -1).join(', ') + ' and ' + names[names.length - 1];
}
function escapeRegex(t) { return t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
function detectGreeting(text) {
  const m = /^(?:Hi|Hello|Dear)\s+([^,\s]+(?:\s+and\s+[^,\s]+)?)/i.exec(text || '');
  return m ? m[1] : '';
}
function swapGreeting(text, from, to) {
  if (!from || from === to) return text;
  return text.replace(new RegExp('^(Hi|Hello|Dear)(\\s+)' + escapeRegex(from), 'i'), (m, g, sp) => g + sp + to);
}
function ticked() { return [...notifyMail.selected].sort((x, y) => x - y).map(i => notifyMail.data.contacts[i]); }
function emailTargets() { return ticked().filter(c => c.email); }
function waTargets() { return ticked().filter(c => c.whatsapp); }

function recipientsBlock(data) {
  const contacts = data.contacts || [];
  if (!contacts.length) {
    return `<div class="tmpl-block"><div class="tmpl-head"><div class="tmpl-head-label">Send to</div></div>
      <p class="small muted" style="margin:10px 0 0;">No contacts yet. <a href="/clients/${encodeURIComponent(data.client_slug)}/profile" target="_blank">Add one in Client Profile</a>.</p></div>`;
  }
  return `<div class="tmpl-block" id="rcpt-block">
    <div class="tmpl-head"><div class="tmpl-head-label">Send to</div><button type="button" class="link-btn small" onclick="selectAllRecipients()">Select all</button></div>
    <div class="rcpt-list" id="rcpt-list"></div>
    <div class="small" style="margin-top:8px;"><a href="/clients/${encodeURIComponent(data.client_slug)}/profile" target="_blank">+ Add a contact</a> or edit numbers in Client Profile.</div>
  </div>`;
}

function renderRecipients() {
  const box = document.getElementById('rcpt-list');
  if (!box || !notifyMail) return;
  const contacts = notifyMail.data.contacts || [];
  box.innerHTML = contacts.map((c, i) => {
    const on = notifyMail.selected.has(i);
    const waText = c.whatsapp ? escapeHtml(c.whatsapp) : (c.whatsapp_number ? '<span class="rcpt-miss">not OK to message</span>' : '<span class="rcpt-miss">no number</span>');
    const mailText = c.email ? escapeHtml(c.email) : '<span class="rcpt-miss">no email</span>';
    return `<button type="button" class="rcpt-row" onclick="toggleRecipient(${i})" aria-pressed="${on}">
      <span class="rcpt-cb ${on ? 'on' : ''}">${on ? tickSvg : ''}</span>
      <span class="rcpt-name">${escapeHtml(c.name)}</span>
      <span class="rcpt-mail">${mailText}</span>
      <span class="rcpt-wa">${waText}</span>
    </button>`;
  }).join('');
  updateSendButtons();
}

function toggleRecipient(i) {
  if (notifyMail.selected.has(i)) notifyMail.selected.delete(i); else notifyMail.selected.add(i);
  syncGreeting();
  renderRecipients();
}
function selectAllRecipients() {
  const all = notifyMail.data.contacts.length;
  if (notifyMail.selected.size === all) notifyMail.selected.clear();
  else notifyMail.data.contacts.forEach((c, i) => notifyMail.selected.add(i));
  syncGreeting();
  renderRecipients();
}

function syncGreeting() {
  if (!notifyMail) return;
  const names = ticked().map(c => firstName(c.name));
  const next = greetingFor(names);
  const body = document.getElementById('mail-body');
  if (body) body.value = swapGreeting(body.value, notifyMail.greeting, next);
  notifyMail.greeting = next;
}

function updateSendButtons() {
  const mailBtn = document.getElementById('mail-send-btn');
  const n = emailTargets().length;
  if (mailBtn) {
    mailBtn.textContent = n ? `Send email to ${n}` : 'Send email';
    mailBtn.disabled = !n;
  }
  const waBtn = document.getElementById('wa-send-btn');
  const w = waTargets().length;
  if (waBtn) {
    waBtn.textContent = w ? `Send WhatsApp to ${w}` : 'Send WhatsApp';
    waBtn.disabled = !w;
  }
  const hint = document.getElementById('rcpt-hint');
  if (hint) {
    const t = ticked();
    const skipMail = t.filter(c => !c.email).length, skipWa = t.filter(c => !c.whatsapp).length;
    hint.textContent = t.length ? [skipMail ? `${skipMail} skipped for email` : '', skipWa ? `${skipWa} skipped for WhatsApp` : ''].filter(Boolean).join(', ') : 'Tick who should receive this.';
  }
}

function mailEmailBlock(data) {
  const envelope = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="M2 6l10 7 10-7" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  if (!data.can_send) {
    const reconnect = data.connected;
    const banner = `<div class="mail-banner">
      <div><b>${reconnect ? 'Reconnect Google to allow sending email.' : 'Connect Google to send email from here.'}</b> The app asks for permission to send only. It cannot read your mail.</div>
      ${data.can_connect ? `<a class="btn btn-sm" href="/calendar/connect">${reconnect ? 'Reconnect Google' : 'Connect Google'}</a>` : '<span class="small muted">Ask an Administrator</span>'}
    </div>`;
    return `<div class="tmpl-block">
      <div class="tmpl-head"><div class="tmpl-head-label">${envelope} Email</div></div>
      ${banner}
      <div class="tmpl-subject-row"><div class="tmpl-subject-text">${escapeHtml(data.subject)}</div>
        <button type="button" class="copy-btn" onclick="copyNotifyField('subject')">${copyIconSvg} Copy</button></div>
      <div class="tmpl-body-row"><div class="tmpl-body-text">${linkifyPortalUrl(escapeHtml(data.email_body))}</div>
        <button type="button" class="copy-btn" onclick="copyNotifyField('email_body')">${copyIconSvg} Copy</button></div>
    </div>`;
  }
  const noEdit = data.can_edit === false;
  return `<div class="tmpl-block" id="mail-block">
    <div class="tmpl-head"><div class="tmpl-head-label">${envelope} Email</div></div>
    <div class="mail-row"><span class="mail-k">From</span><span>${escapeHtml(data.sender)}</span><span class="mail-tag ok">Connected</span></div>
    <div class="mail-row"><span class="mail-k">Cc</span><input type="text" id="mail-cc" class="mail-input" placeholder="Add Cc (separate with commas)"><label class="mail-bcc"><input type="checkbox" id="mail-bcc"> Bcc me</label></div>
    <div class="mail-row"><span class="mail-k">Subject</span><input type="text" id="mail-subject" class="mail-input" value="${escapeHtml(data.subject)}"></div>
    <textarea id="mail-body" class="mail-body" rows="9">${escapeHtml(data.email_body)}</textarea>
    <div class="small muted" style="margin-top:6px;">The message is editable. AI drafted it, so read it before sending.</div>
    <div id="mail-actions" class="mail-actions">
      ${noEdit ? '<span class="small muted">You have view-only access to this client, so you can copy the message but not send it.</span>' : '<button type="button" class="btn" id="mail-send-btn" onclick="showMailConfirm()">Send email</button>'}
      <button type="button" class="btn btn-secondary" onclick="copyMailMessage()">Copy message</button>
    </div>
    <div id="mail-confirm" class="mail-confirm" style="display:none;"></div>
    <div id="mail-status" class="small" style="margin-top:8px;"></div>
  </div>`;
}

function whatsappBlock(data) {
  return `<div class="tmpl-block">
    <div class="tmpl-head"><div class="tmpl-head-label">${waSvg} WhatsApp</div>
      <button type="button" class="copy-btn" onclick="copyNotifyField('whatsapp_body')">${copyIconSvg} Copy</button></div>
    <div class="tmpl-body">${linkifyPortalUrl(escapeHtml(data.whatsapp_body))}</div>
    <div class="mail-actions">
      <button type="button" class="btn btn-wa" id="wa-send-btn" onclick="sendWhatsApp()">Send WhatsApp</button>
    </div>
    <div class="small muted" style="margin-top:8px;">Opens WhatsApp with the message ready for each person. You press Send there.</div>
    <div id="wa-links" class="wa-links"></div>
  </div>`;
}

function waLink(contact) {
  const text = swapGreeting(notifyMail.data.whatsapp_body, detectGreeting(notifyMail.data.whatsapp_body), firstName(contact.name));
  return 'https://wa.me/' + contact.whatsapp.replace(/\D/g, '') + '?text=' + encodeURIComponent(text);
}
function sendWhatsApp() {
  const targets = waTargets();
  const box = document.getElementById('wa-links');
  box.innerHTML = '';
  if (!targets.length) return;
  if (targets.length === 1) { window.open(waLink(targets[0]), '_blank', 'noopener'); return; }
  box.innerHTML = targets.map(c => `<a class="wa-link" href="${waLink(c)}" target="_blank" rel="noopener">Message ${escapeHtml(firstName(c.name))} (${escapeHtml(c.whatsapp)})</a>`).join('');
}

function mailRecipients() {
  const to = emailTargets().map(c => c.email);
  const cc = (document.getElementById('mail-cc').value || '').split(/[,;\s]+/).map(v => v.trim()).filter(Boolean);
  return { to, cc };
}

function showMailConfirm() {
  const { to } = mailRecipients();
  const status = document.getElementById('mail-status');
  status.textContent = '';
  if (!to.length) { status.innerHTML = '<span class="mail-err">Choose at least one person to send to.</span>'; return; }
  if (to.length > 5) { status.innerHTML = '<span class="mail-err">Send to at most 5 people at once.</span>'; return; }
  const names = emailTargets().map(c => c.name);
  const who = names.length === 1 ? names[0] : names.length + ' people';
  const box = document.getElementById('mail-confirm');
  box.innerHTML = `<div class="mail-confirm-text"><div class="mail-confirm-title">Send this email to ${escapeHtml(who)}?</div>
    <div class="small">From ${escapeHtml(notifyMail.data.sender)}. It cannot be recalled once sent.</div></div>
    <button type="button" class="btn btn-sm" id="mail-go" onclick="sendNotifyEmail()">Send now</button>
    <button type="button" class="link-btn small muted" onclick="hideMailConfirm()">Back to edit</button>`;
  box.style.display = 'flex';
  document.getElementById('mail-actions').style.display = 'none';
}
function hideMailConfirm() {
  document.getElementById('mail-confirm').style.display = 'none';
  document.getElementById('mail-actions').style.display = 'flex';
}

async function sendNotifyEmail() {
  const btn = document.getElementById('mail-go');
  const status = document.getElementById('mail-status');
  btn.disabled = true;
  const { to, cc } = mailRecipients();
  const d = notifyMail.data;
  try {
    const res = await fetch('/notify/send-email', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
      body: JSON.stringify({
        client_id: d.client_id, to, cc, bcc_me: document.getElementById('mail-bcc').checked,
        subject: document.getElementById('mail-subject').value, body: document.getElementById('mail-body').value,
        entity_type: notifyEntity.type, entity_id: notifyEntity.id,
      }),
    });
    let out = {};
    try { out = await res.json(); } catch (e) { /* non-JSON error */ }
    if (!res.ok || !out.ok) {
      hideMailConfirm();
      const extra = out.reconnect ? ' <a href="/settings/integrations">Open Integrations</a>' : '';
      status.innerHTML = `<span class="mail-err">${escapeHtml(out.error || 'Email was not sent. Nothing left the app. Try again in a minute.')}</span>${extra}`;
      return;
    }
    const names = emailTargets().map(c => c.name).join(', ');
    document.getElementById('mail-block').innerHTML = `<div class="tmpl-head"><div class="tmpl-head-label">Email</div><span class="mail-tag ok">Email sent</span></div>
      <p class="small" style="margin:10px 0 0; line-height:1.6;">Sent to <b>${escapeHtml(names)}</b> from ${escapeHtml(out.sender)}. It is in that account's Sent folder, and replies will arrive there.</p>`;
    showToast('Email sent to ' + names);
  } catch (e) {
    hideMailConfirm();
    status.innerHTML = '<span class="mail-err">Email was not sent. Nothing left the app. Try again in a minute.</span>';
  }
}

async function copyMailMessage() {
  const text = document.getElementById('mail-subject').value + '\n\n' + document.getElementById('mail-body').value;
  try { await navigator.clipboard.writeText(text); showToast('Email copied'); }
  catch (e) { showToast('Copy is blocked in this browser. Select the text and copy it.'); }
}

function renderNotifyTemplates(data) {
  const contacts = data.contacts || [];
  const primary = contacts.findIndex(c => c.primary);
  notifyMail = { data, selected: new Set(contacts.length ? [primary >= 0 ? primary : 0] : []), greeting: detectGreeting(data.email_body) || data.greeting_name || '' };
  document.getElementById('notify-modal-body').innerHTML =
    recipientsBlock(data) + '<div class="small rcpt-hint" id="rcpt-hint"></div>' + mailEmailBlock(data) + whatsappBlock(data);
  syncGreeting();
  renderRecipients();
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
