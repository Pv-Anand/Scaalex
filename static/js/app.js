function toggleSidebar() {
  document.querySelector('.sidebar').classList.toggle('open');
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
  editRow.style.display = open ? 'none' : 'table-row';
  toggleBtn.classList.toggle('open', !open);
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
    selectEl.closest('tr')?.classList.toggle('is-completed', status === 'Completed');
  }
}
