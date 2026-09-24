/* Data Room: folder management, filing, client visibility, undo toasts.
   Server-rendered pages; every change is a small JSON POST followed by a
   reload, with an Undo/Notify toast carried across the reload. */
const DR = (function () {
  const dataEl = document.getElementById('dr-data');
  const state = dataEl ? JSON.parse(dataEl.textContent) : {};
  const base = `/clients/${state.slug}/data-room`;
  let popover = null;
  let popoverAnchor = null;
  let popoverOpts = null;
  let panelDirty = false;

  const esc = (s) => escapeHtml(String(s));
  const byId = (id) => (state.folders || []).find((f) => f.id === Number(id));

  // ------------------------------------------------------------ http + toast
  async function post(path, body) {
    let res, data;
    try {
      res = await fetch(base + path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify(body || {}),
      });
      data = await res.json();
    } catch (err) {
      showToast('Something went wrong. Please try again.');
      throw err;
    }
    if (!data.ok) {
      showToast(data.error || 'Something went wrong.');
      throw new Error(data.error || 'request failed');
    }
    return data;
  }

  function renderToast(item) {
    document.querySelectorAll('.dr-toast').forEach((el) => el.remove());
    const el = document.createElement('div');
    el.className = 'dr-toast';
    el.innerHTML = `<span>${item.message}</span>`;
    if (item.undo && item.undo.length) {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = 'Undo';
      b.onclick = async () => {
        b.disabled = true;
        try {
          for (const req of item.undo) await post(req.path, req.body);
          remember({ message: 'Undone.' });
          location.reload();
        } catch (e) { b.disabled = false; }
      };
      el.appendChild(b);
    }
    if (item.notify && offerNotify()) {
      const n = document.createElement('button');
      n.type = 'button';
      n.textContent = 'Notify client';
      n.onclick = () => { el.remove(); notify(item.notify.kind, item.notify.id); };
      el.appendChild(n);
    }
    document.body.appendChild(el);
    setTimeout(() => el.remove(), item.notify || item.undo ? 9000 : 3500);
  }

  function remember(item) {
    try { sessionStorage.setItem('drToast', JSON.stringify(item)); } catch (e) { /* private mode */ }
  }

  function commit(item, opts) {
    if (opts && opts.noReload) { renderToast(item); return; }
    remember(item);
    location.reload();
  }

  function offerNotify() {
    try { return localStorage.getItem('drOfferNotify') !== '0'; } catch (e) { return true; }
  }

  function notify(kind, id) {
    const dataset = kind === 'folder'
      ? { entityType: 'data-room-folder', entityId: id, state: 'completed', eyebrow: 'Data Room · New for the client',
          title: `Notify ${state.clientName}`, subtitle: (byId(id) || {}).name || 'Data Room folder' }
      : { entityType: 'data-room', entityId: state.clientId, state: 'completed', eyebrow: 'Data Room · Now available',
          title: `Notify ${state.clientName}`, subtitle: 'Their Data Room is ready' };
    openNotifyModal({ dataset });
  }

  // ------------------------------------------------------------ popovers
  function closePopover() {
    if (popover) { popover.remove(); popover = null; }
    if (panelDirty) { panelDirty = false; location.reload(); }
  }

  function place(el, anchor, opts) {
    el.style.maxHeight = '';
    const r = anchor.getBoundingClientRect();
    const width = el.offsetWidth;
    let left = Math.min(Math.max(8, r.left), window.innerWidth - width - 8);
    if (opts && opts.alignRight) left = Math.max(8, r.right - width);
    const below = window.innerHeight - r.bottom - 14;
    const above = r.top - 14;
    const height = el.offsetHeight;
    let top;
    if (height <= below || below >= above) {
      top = r.bottom + 6;
      el.style.maxHeight = Math.max(160, below) + 'px';
    } else {
      const room = Math.max(160, above);
      el.style.maxHeight = room + 'px';
      top = Math.max(8, r.top - Math.min(height, room) - 6);
    }
    el.style.left = left + 'px';
    el.style.top = top + 'px';
  }

  function openPopover(anchor, html, opts) {
    closePopover();
    const el = document.createElement('div');
    el.className = 'dr-pop' + ((opts && opts.wide) ? ' wide' : '');
    el.innerHTML = html;
    document.body.appendChild(el);
    popover = el;
    popoverAnchor = anchor;
    popoverOpts = opts;
    place(el, anchor, opts);
    el.addEventListener('click', (e) => e.stopPropagation());
    return el;
  }

  document.addEventListener('click', (e) => {
    if (popover && !e.target.closest('.dr-pop') && !(popoverAnchor && popoverAnchor.contains(e.target))) closePopover();
  });

  // ------------------------------------------------------------ visibility
  async function setVisibility(id, visible) {
    const f = byId(id);
    await post(`/folders/${id}/visibility`, { visible });
    commit({
      message: visible ? `<b>${esc(f.name)}</b> is now visible to ${esc(state.clientName)}` : `<b>${esc(f.name)}</b> is now hidden from the client`,
      undo: [{ path: `/folders/${id}/visibility`, body: { visible: !visible } }],
      notify: visible ? { kind: 'folder', id } : null,
    });
  }

  function toggleVisibility(id) {
    const f = byId(id);
    if (f && !f.hidden_by_ancestor) setVisibility(id, !f.visible);
  }

  async function bulkVisibility(visible) {
    const ids = selectedFolderIds();
    if (!ids.length) return;
    const data = await post('/folders/bulk-visibility', { folder_ids: ids, visible });
    commit({
      message: `${data.changed.length} folder${data.changed.length === 1 ? '' : 's'} ${visible ? 'shown to' : 'hidden from'} the client`,
      undo: [{ path: '/folders/bulk-visibility', body: { folder_ids: data.changed, visible: !visible } }],
    });
  }

  // ------------------------------------------------------------ folder CRUD
  function rowFor(id) { return document.querySelector(`.dr-row[data-id="${id}"]`); }

  function newFolder(parentId) {
    const rows = document.getElementById('dr-rows');
    if (!rows) return;
    document.querySelectorAll('.dr-row-new').forEach((r) => r.remove());
    const depth = parentId ? (byId(parentId).depth + 1) : 0;
    const row = document.createElement('div');
    row.className = 'dr-row dr-row-new';
    row.style.paddingLeft = 8 + depth * 20 + 'px';
    row.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z" stroke-linejoin="round"/></svg><input type="text" maxlength="200" placeholder="Folder name" aria-label="New folder name"><span class="dr-hint">Enter to save</span>';
    if (parentId) {
      const after = lastRowInBranch(parentId);
      (after || rowFor(parentId)).after(row);
    } else {
      rows.appendChild(row);
    }
    const input = row.querySelector('input');
    input.focus();
    let done = false;
    input.addEventListener('keydown', async (e) => {
      if (e.key === 'Escape') { done = true; row.remove(); }
      if (e.key === 'Enter') {
        e.preventDefault();
        const name = input.value.trim();
        if (!name) { done = true; row.remove(); return; }
        done = true;
        try {
          const data = await post('/folders', { name, parent_id: parentId });
          commit({ message: `Folder <b>${esc(name)}</b> created`, undo: [{ path: `/folders/${data.folder.id}/delete`, body: {} }] });
        } catch (err) { done = false; input.focus(); }
      }
    });
    input.addEventListener('blur', () => { if (!done && !input.value.trim()) row.remove(); });
  }

  function lastRowInBranch(id) {
    const ids = new Set([id]);
    (state.folders || []).forEach((f) => { if (ids.has(f.parent_id)) ids.add(f.id); });
    let last = null;
    document.querySelectorAll('.dr-row[data-id]').forEach((r) => { if (ids.has(Number(r.dataset.id))) last = r; });
    return last;
  }

  function renameFolder(id) {
    closePopover();
    const row = rowFor(id);
    const f = byId(id);
    if (!row || !f) return;
    const nameEl = row.querySelector('.dr-row-name');
    const input = document.createElement('input');
    input.type = 'text';
    input.value = f.name;
    input.className = 'dr-rename';
    input.maxLength = 200;
    nameEl.replaceWith(input);
    row.querySelector('.dr-row-link').addEventListener('click', (e) => e.preventDefault(), { once: true });
    input.focus();
    input.select();
    let done = false;
    const cancel = () => { if (!done) { done = true; input.replaceWith(nameEl); } };
    input.addEventListener('keydown', async (e) => {
      e.stopPropagation();
      if (e.key === 'Escape') cancel();
      if (e.key === 'Enter') {
        e.preventDefault();
        const name = input.value.trim();
        if (!name || name === f.name) { cancel(); return; }
        done = true;
        try {
          await post(`/folders/${id}/rename`, { name });
          commit({ message: `Renamed to <b>${esc(name)}</b>`, undo: [{ path: `/folders/${id}/rename`, body: { name: f.name } }] });
        } catch (err) { done = false; input.focus(); }
      }
    });
    input.addEventListener('blur', cancel);
  }

  async function deleteFolder(id) {
    closePopover();
    const data = await post(`/folders/${id}/delete`, {});
    commit({
      message: `<b>${esc(data.name)}</b> deleted.${data.doc_count ? ` ${data.doc_count} document${data.doc_count === 1 ? '' : 's'} moved to All Documents as Not filed.` : ''}`,
      undo: [{ path: '/restore', body: { folder_ids: data.undo.folder_ids, doc_map: data.undo.doc_map } }],
    });
  }

  async function bulkDelete() {
    const ids = selectedFolderIds();
    if (!ids.length) return;
    const folderIds = new Set();
    const docMap = {};
    let docs = 0;
    for (const id of ids) {
      if (!byId(id)) continue;
      try {
        const data = await post(`/folders/${id}/delete`, {});
        data.undo.folder_ids.forEach((x) => folderIds.add(x));
        Object.assign(docMap, data.undo.doc_map);
        docs += data.doc_count;
      } catch (e) { /* already removed with a parent */ }
    }
    commit({
      message: `${folderIds.size} folder${folderIds.size === 1 ? '' : 's'} deleted.${docs ? ` ${docs} document${docs === 1 ? '' : 's'} moved to All Documents as Not filed.` : ''}`,
      undo: [{ path: '/restore', body: { folder_ids: [...folderIds], doc_map: docMap } }],
    });
  }

  async function moveFolder(id, parentId, beforeId, quiet) {
    const data = await post(`/folders/${id}/move`, { parent_id: parentId, before_id: beforeId });
    const item = {
      message: `<b>${esc(byId(id).name)}</b> moved`,
      undo: [{ path: `/folders/${id}/move`, body: data.previous }],
    };
    if (!quiet) commit(item);
    return item;
  }

  function moveFolderPicker(id, anchor) {
    const banned = new Set([id]);
    (state.folders || []).forEach((f) => { if (banned.has(f.parent_id)) banned.add(f.id); });
    folderPicker(anchor, { title: 'Move to', includeRoot: true, exclude: banned, onPick: (target) => moveFolder(id, target, null) });
  }

  function bulkMove(anchor) {
    const ids = selectedFolderIds();
    if (!ids.length) return;
    const banned = new Set(ids);
    (state.folders || []).forEach((f) => { if (banned.has(f.parent_id)) banned.add(f.id); });
    folderPicker(anchor, {
      title: 'Move folders to', includeRoot: true, exclude: banned,
      onPick: async (target) => {
        const undo = [];
        for (const id of ids) {
          try { const item = await moveFolder(id, target, null, true); undo.push(...item.undo); } catch (e) { /* skip */ }
        }
        commit({ message: `${undo.length} folder${undo.length === 1 ? '' : 's'} moved`, undo });
      },
    });
  }

  // ------------------------------------------------------------ pickers + menus
  function folderPicker(anchor, opts) {
    const folders = (state.folders || []).filter((f) => !(opts.exclude && opts.exclude.has(f.id)));
    let html = `<div class="dr-pop-title">${esc(opts.title)}</div><input type="text" class="dr-pop-search" placeholder="Search folders..." aria-label="Search folders"><div class="dr-pop-list">`;
    if (opts.includeRoot) html += '<button type="button" class="dr-pop-item" data-target="">Top level (Data Room)</button>';
    folders.forEach((f) => {
      html += `<button type="button" class="dr-pop-item" data-target="${f.id}" data-name="${esc(f.name.toLowerCase())}" style="padding-left:${12 + f.depth * 16}px;">${esc(f.name)}</button>`;
    });
    if (opts.allowNone) html += '<hr><button type="button" class="dr-pop-item" data-target="none">Remove from Data Room (not filed)</button>';
    if (opts.allowCreate) html += '<hr><button type="button" class="dr-pop-item" data-target="new">+ New folder here...</button>';
    html += '</div>';
    const el = openPopover(anchor, html);
    const search = el.querySelector('.dr-pop-search');
    search.focus();
    search.addEventListener('input', () => {
      const q = search.value.trim().toLowerCase();
      el.querySelectorAll('.dr-pop-item[data-name]').forEach((b) => { b.style.display = !q || b.dataset.name.includes(q) ? '' : 'none'; });
    });
    el.querySelectorAll('.dr-pop-item').forEach((b) => b.addEventListener('click', async () => {
      const t = b.dataset.target;
      closePopover();
      if (t === 'new') {
        const name = (window.prompt('Folder name') || '').trim();
        if (!name) return;
        const data = await post('/folders', { name, parent_id: null });
        opts.onPick(data.folder.id);
      } else {
        opts.onPick(t === '' || t === 'none' ? null : Number(t));
      }
    }));
  }

  function folderMenu(id, anchor) {
    const f = byId(id);
    const shown = f.visible && !f.hidden_by_ancestor;
    const html = `<div class="dr-pop-list">
      <button type="button" class="dr-pop-item" data-act="rename">Rename<span class="k">F2</span></button>
      <button type="button" class="dr-pop-item" data-act="move">Move to...</button>
      <button type="button" class="dr-pop-item" data-act="sub">New subfolder</button>
      <button type="button" class="dr-pop-item" data-act="vis" ${f.hidden_by_ancestor ? 'disabled' : ''}>${shown ? 'Hide from client' : 'Show to client'}</button>
      ${shown && state.master ? '<button type="button" class="dr-pop-item" data-act="notify">Notify client...</button>' : ''}
      <hr><button type="button" class="dr-pop-item danger" data-act="delete">Delete folder</button></div>`;
    const el = openPopover(anchor, html, { alignRight: true });
    el.querySelectorAll('[data-act]').forEach((b) => b.addEventListener('click', () => {
      const act = b.dataset.act;
      closePopover();
      if (act === 'rename') renameFolder(id);
      if (act === 'move') moveFolderPicker(id, anchor);
      if (act === 'sub') newFolder(id);
      if (act === 'vis') setVisibility(id, !shown);
      if (act === 'notify') notify('folder', id);
      if (act === 'delete') deleteFolder(id);
    }));
  }

  function docMenu(docId, anchor) {
    const html = `<div class="dr-pop-list">
      <button type="button" class="dr-pop-item" data-act="move">Move to folder...</button>
      <button type="button" class="dr-pop-item" data-act="remove">Remove from Data Room</button>
      <button type="button" class="dr-pop-item danger" data-act="delete">Delete document...</button></div>`;
    const el = openPopover(anchor, html, { alignRight: true });
    el.querySelectorAll('[data-act]').forEach((b) => b.addEventListener('click', () => {
      const act = b.dataset.act;
      closePopover();
      if (act === 'move') pickFolder([docId], anchor, {});
      if (act === 'remove') moveDocs([docId], null, {});
      if (act === 'delete') confirmDeleteDocs([docId], anchor);
    }));
  }

  // ------------------------------------------------------------ documents
  function confirmDeleteDocs(docIds, anchor) {
    if (!docIds.length) return;
    const n = docIds.length;
    const what = n === 1 ? 'this document' : `these ${n} documents`;
    const el = openPopover(anchor, `<div class="dr-confirm">
      <div class="dr-confirm-title">Delete ${what}?</div>
      <p>${n === 1 ? 'The file is' : 'The files are'} removed for good, from the Data Room and from All Documents. This cannot be undone.</p>
      <div class="dr-confirm-actions"><button type="button" class="btn btn-sm btn-danger-solid" data-go>Delete</button><button type="button" class="link-btn small muted" data-cancel>Cancel</button></div>
    </div>`, {});
    el.querySelector('[data-cancel]').addEventListener('click', closePopover);
    el.querySelector('[data-go]').addEventListener('click', async (ev) => {
      ev.target.disabled = true;
      try {
        const data = await post('/documents/delete', { doc_ids: docIds });
        closePopover();
        commit({ message: `${data.deleted} document${data.deleted === 1 ? '' : 's'} deleted` });
      } catch (e) { ev.target.disabled = false; }
    });
  }

  function pickFolder(docIds, anchor, opts) {
    if (!docIds.length) return;
    if (!(state.folders || []).length) { showToast('Create a Data Room folder first.'); return; }
    folderPicker(anchor, {
      title: `File ${docIds.length} document${docIds.length === 1 ? '' : 's'} in`, allowNone: true, allowCreate: true,
      onPick: (target) => moveDocs(docIds, target, opts || {}),
    });
  }

  async function moveDocs(docIds, folderId, opts) {
    if (!docIds.length) return;
    const data = await post('/documents/move', { doc_ids: docIds, folder_id: folderId });
    const grouped = {};
    Object.entries(data.previous).forEach(([docId, prev]) => { (grouped[prev === null ? 'null' : prev] = grouped[prev === null ? 'null' : prev] || []).push(Number(docId)); });
    const undo = Object.entries(grouped).map(([prev, ids]) => ({
      path: '/documents/move', body: { doc_ids: ids, folder_id: prev === 'null' ? null : Number(prev) },
    }));
    const folder = folderId ? byId(folderId) : null;
    commit({
      message: folder ? `${data.moved} document${data.moved === 1 ? '' : 's'} filed in <b>${esc(folder.name)}</b>` : `${data.moved} document${data.moved === 1 ? '' : 's'} removed from the Data Room`,
      undo,
    });
  }

  function selectedDocIds() { return [...document.querySelectorAll('.doc-select:checked')].map((c) => Number(c.value)); }
  function onDocSelect() {
    const n = selectedDocIds().length;
    const bar = document.getElementById('doc-bulkbar');
    if (bar) { bar.style.display = n ? 'flex' : 'none'; document.getElementById('doc-bulk-n').textContent = n; }
    document.querySelectorAll('tr[data-doc-id]').forEach((tr) => tr.classList.toggle('selected', tr.querySelector('.doc-select')?.checked));
  }
  function toggleAllDocs(on) { document.querySelectorAll('.doc-select').forEach((c) => { c.checked = on; }); onDocSelect(); }
  function clearDocSelection() { toggleAllDocs(false); document.querySelectorAll('.dr-cbx').forEach((c) => { c.checked = false; }); }

  function selectedFolderIds() { return [...document.querySelectorAll('.dr-row-check:checked')].map((c) => Number(c.closest('.dr-row').dataset.id)); }
  function onFolderSelect() {
    const n = selectedFolderIds().length;
    const bar = document.getElementById('dr-folder-bulk');
    if (bar) { bar.style.display = n ? 'flex' : 'none'; document.getElementById('dr-bulk-n').textContent = n; }
    document.getElementById('dr-tree')?.classList.toggle('selecting', n > 0);
  }

  async function uploadFiles(files, folderId) {
    if (!files || !files.length) return;
    const form = new FormData();
    [...files].forEach((f) => form.append('files', f));
    if (folderId) form.append('folder_id', folderId);
    showToast(`Uploading ${files.length} file${files.length === 1 ? '' : 's'}...`);
    try {
      const res = await fetch(base + '/upload', { method: 'POST', headers: { 'X-CSRFToken': csrfToken() }, body: form });
      const data = await res.json();
      if (!data.ok) { showToast(data.error || 'Upload failed.'); return; }
      const skipped = data.errors && data.errors.length ? ` ${data.errors.length} skipped (unsupported type).` : '';
      commit({ message: `${data.saved} file${data.saved === 1 ? '' : 's'} uploaded.${skipped}` });
    } catch (e) { showToast('Upload failed. Please try again.'); }
  }

  // ------------------------------------------------------------ templates + master + access
  // Service-based structure picker (empty Data Room, or "Add services" later).
  const picker = { sel: new Set(), scratch: false, data: null };

  function initPicker() {
    const el = document.getElementById('dr-picker-data');
    if (!el) return;
    try { picker.data = JSON.parse(el.textContent); } catch (e) { return; }
    (picker.data.preselected || []).forEach(k => picker.sel.add(k));
    renderPicker();
  }

  function buildStructure(keys) {
    const d = picker.data;
    const chosen = d.order.filter(k => keys.has(k));
    if (!chosen.length) return [];
    const merged = {};
    chosen.forEach(k => {
      Object.entries(d.services[k].folders).forEach(([name, subs]) => {
        const bucket = merged[name] = merged[name] || [];
        subs.forEach(x => { if (bucket.indexOf(x) < 0) bucket.push(x); });
      });
    });
    const out = [{ name: d.engagement, subs: [], visible: true }];
    d.topOrder.forEach(name => { if (merged[name]) out.push({ name, subs: merged[name], visible: true }); });
    out.push({ name: d.workingPapers, subs: [], visible: false });
    return out;
  }

  const EYE_ON = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
  const EYE_OFF = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M3 3l18 18M10.6 5.1A10 10 0 0112 5c6.4 0 10 7 10 7a17 17 0 01-3.2 4M6.4 6.5C3.6 8.3 2 12 2 12s3.6 7 10 7c1.6 0 3-.4 4.2-1"/></svg>';

  function renderPicker() {
    if (!picker.data) return;
    document.querySelectorAll('.dr-svc').forEach(b => {
      const on = picker.sel.has(b.dataset.key);
      b.classList.toggle('sel', on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    const scratch = document.getElementById('dr-scratch');
    if (scratch) { scratch.classList.toggle('sel', picker.scratch); scratch.setAttribute('aria-pressed', picker.scratch ? 'true' : 'false'); }

    const list = document.getElementById('dr-prev-list');
    const sub = document.getElementById('dr-prev-sub');
    const note = document.getElementById('dr-prev-note');
    const btn = document.getElementById('dr-create-btn');
    list.innerHTML = '';
    if (picker.scratch) {
      sub.textContent = 'Empty';
      note.textContent = '';
      list.innerHTML = '<div class="dr-prev-empty"><strong>An empty Data Room</strong><span>You will land on the folder tree with the first folder name ready to type.</span></div>';
      btn.textContent = 'Create empty Data Room'; btn.disabled = false;
      return;
    }
    const structure = buildStructure(picker.sel);
    if (!structure.length) {
      sub.textContent = '';
      note.textContent = '';
      list.innerHTML = '<div class="dr-prev-empty"><span>Tick a service to see the folders that will be created.</span></div>';
      btn.textContent = 'Create folders'; btn.disabled = true;
      return;
    }
    const total = structure.reduce((n, f) => n + 1 + f.subs.length, 0);
    sub.textContent = picker.data.order.filter(k => picker.sel.has(k)).map(k => picker.data.services[k].name).join(' + ');
    note.textContent = `${total} folders. The eye shows what the client will see once it is on.`;
    structure.forEach(f => {
      const row = document.createElement('div');
      row.className = 'dr-prev-row top' + (f.visible ? '' : ' hid');
      row.innerHTML = `<span class="n">${esc(f.name)}</span><span class="c">${f.subs.length || ''}</span><span class="eye ${f.visible ? '' : 'off'}">${f.visible ? EYE_ON : EYE_OFF}</span>`;
      list.appendChild(row);
      f.subs.forEach(name => {
        const r = document.createElement('div');
        r.className = 'dr-prev-row sub';
        r.innerHTML = `<span class="n">${esc(name)}</span><span class="eye">${EYE_ON}</span>`;
        list.appendChild(r);
      });
    });
    btn.textContent = picker.data.picker ? `Add missing folders` : `Create ${total} folders`;
    btn.disabled = false;
  }

  function toggleService(key) {
    picker.scratch = false;
    if (picker.sel.has(key)) picker.sel.delete(key); else picker.sel.add(key);
    renderPicker();
  }

  function chooseScratch() {
    picker.scratch = true;
    picker.sel.clear();
    renderPicker();
  }

  async function createStructure() {
    if (picker.scratch) { startBlank(); return; }
    const services = picker.data.order.filter(k => picker.sel.has(k));
    if (!services.length) return;
    const btn = document.getElementById('dr-create-btn');
    btn.disabled = true;
    try {
      const data = await post('/folders/template', { services });
      remember({ message: `${data.created} folder${data.created === 1 ? '' : 's'} created. Rename or delete what you do not need.` });
      location.href = location.pathname;
    } catch (e) { btn.disabled = false; showToast('Could not create the folders. Please try again.'); }
  }

  async function startBlank() {
    const data = await post('/folders', { name: 'New folder', parent_id: null });
    try { sessionStorage.setItem('drRename', String(data.folder.id)); } catch (e) { /* ignore */ }
    location.reload();
  }

  async function setMaster(enabled) {
    await post('/master', { enabled });
    commit({
      message: enabled ? `Data Room turned on for <b>${esc(state.clientName)}</b>` : `Data Room turned off for <b>${esc(state.clientName)}</b>`,
      undo: [{ path: '/master', body: { enabled: !enabled } }],
      notify: enabled ? { kind: 'client' } : null,
    });
  }

  async function setContactAccess(contactId, enabled, name) {
    await post(`/access/${contactId}`, { enabled });
    commit({
      message: enabled ? `<b>${esc(name)}</b> can now see the Data Room` : `<b>${esc(name)}</b> no longer has Data Room access`,
      undo: [{ path: `/access/${contactId}`, body: { enabled: !enabled } }],
      notify: enabled ? { kind: 'client' } : null,
    });
  }

  // ------------------------------------------------------------ sharing panel
  function effectiveVisible(f) {
    let cur = f;
    while (cur) { if (!cur.visible) return false; cur = byId(cur.parent_id); }
    return true;
  }

  function sharePanelHtml() {
    const folders = state.folders || [];
    const seen = folders.filter(effectiveVisible).length;
    let rows = '';
    folders.forEach((f) => {
      const locked = f.hidden_by_ancestor || (f.parent_id && !effectiveVisible(byId(f.parent_id)));
      rows += `<div class="dr-share-row ${effectiveVisible(f) ? '' : 'off'}" style="padding-left:${14 + f.depth * 18}px;">
        <span class="n">${esc(f.name)}</span><span class="c">${f.count}</span>
        <label class="toggle-switch"><input type="checkbox" data-id="${f.id}" ${f.visible ? 'checked' : ''} ${locked ? 'disabled' : ''}><span class="toggle-track"><span class="toggle-knob"></span></span></label></div>`;
    });
    return `<div class="dr-share-head"><div><div class="t">What ${esc(state.clientName)} can see</div><div class="s">${seen} of ${folders.length} folders are visible on their portal${state.master ? '' : ' (once the Data Room is on)'}.</div></div><button type="button" class="dr-x" aria-label="Close">&times;</button></div>
      <div class="dr-share-links"><button type="button" data-all="1">Show all</button><button type="button" data-all="0">Hide all</button><a class="prev" href="${location.pathname}?preview=1${state.selectedId ? '&folder=' + state.selectedId : ''}">Preview as client</a></div>
      <div class="dr-share-list">${rows}</div>
      <label class="dr-share-opt"><input type="checkbox" id="dr-offer-notify" ${offerNotify() ? 'checked' : ''}> Offer to notify the client when I make a folder visible</label>`;
  }

  function openSharePanel(anchor) {
    const render = () => {
      const el = popover;
      el.innerHTML = sharePanelHtml();
      place(el, popoverAnchor, popoverOpts);
      el.querySelector('.dr-x').onclick = closePopover;
      el.querySelector('#dr-offer-notify').onchange = (e) => { try { localStorage.setItem('drOfferNotify', e.target.checked ? '1' : '0'); } catch (x) { /* ignore */ } };
      el.querySelectorAll('.dr-share-row input').forEach((inp) => inp.addEventListener('change', async () => {
        const f = byId(inp.dataset.id);
        try {
          await post(`/folders/${f.id}/visibility`, { visible: inp.checked });
        } catch (e) { inp.checked = !inp.checked; return; }
        f.visible = inp.checked;
        (state.folders || []).forEach((g) => { g.hidden_by_ancestor = !!g.parent_id && !effectiveVisible(byId(g.parent_id)); });
        panelDirty = true;
        render();
        const chipN = document.getElementById('dr-sees-n');
        if (chipN) chipN.textContent = (state.folders || []).filter(effectiveVisible).length;
        renderToast({
          message: inp.checked ? `<b>${esc(f.name)}</b> is now visible to ${esc(state.clientName)}` : `<b>${esc(f.name)}</b> is now hidden from the client`,
          undo: [{ path: `/folders/${f.id}/visibility`, body: { visible: !inp.checked } }],
          notify: inp.checked ? { kind: 'folder', id: f.id } : null,
        });
      }));
      el.querySelectorAll('[data-all]').forEach((b) => b.addEventListener('click', async () => {
        const visible = b.dataset.all === '1';
        const ids = (state.folders || []).map((f) => f.id);
        try { await post('/folders/bulk-visibility', { folder_ids: ids, visible }); } catch (e) { return; }
        (state.folders || []).forEach((f) => { f.visible = visible; f.hidden_by_ancestor = false; });
        panelDirty = true;
        render();
        const chipN = document.getElementById('dr-sees-n');
        if (chipN) chipN.textContent = visible ? ids.length : 0;
      }));
    };
    openPopover(anchor, '', { wide: true });
    render();
  }

  // ------------------------------------------------------------ drag and drop
  let dragging = null;

  function zoneFor(e, row) {
    const r = row.getBoundingClientRect();
    const y = (e.clientY - r.top) / r.height;
    return y < 0.25 ? 'before' : (y > 0.75 ? 'after' : 'inside');
  }

  function clearDropClasses() {
    document.querySelectorAll('.drop-before, .drop-after, .drop-inside').forEach((el) => el.classList.remove('drop-before', 'drop-after', 'drop-inside'));
  }

  function siblingAfter(folder) {
    const sibs = (state.folders || []).filter((f) => f.parent_id === folder.parent_id);
    const i = sibs.findIndex((f) => f.id === folder.id);
    return sibs[i + 1] ? sibs[i + 1].id : null;
  }

  function setupDnD() {
    const tree = document.getElementById('dr-tree');
    if (!tree) return;

    document.addEventListener('dragstart', (e) => {
      const row = e.target.closest && e.target.closest('.dr-row[data-id]');
      const docRow = e.target.closest && e.target.closest('.dr-doc-row');
      if (row && !e.target.closest('input')) { dragging = { type: 'folder', id: Number(row.dataset.id) }; row.classList.add('dragging'); }
      else if (docRow) { dragging = { type: 'doc', id: Number(docRow.dataset.docId) }; docRow.classList.add('dragging'); }
      if (dragging) { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', dragging.type + ':' + dragging.id); }
    });
    document.addEventListener('dragend', () => { dragging = null; clearDropClasses(); document.querySelectorAll('.dragging').forEach((el) => el.classList.remove('dragging')); });

    tree.addEventListener('dragover', (e) => {
      const row = e.target.closest('.dr-row[data-id]');
      if (!row || !dragging) return;
      const target = byId(row.dataset.id);
      if (dragging.type === 'folder') {
        const banned = new Set([dragging.id]);
        (state.folders || []).forEach((f) => { if (banned.has(f.parent_id)) banned.add(f.id); });
        if (banned.has(target.id)) return;
      }
      e.preventDefault();
      clearDropClasses();
      const zone = dragging.type === 'doc' ? 'inside' : zoneFor(e, row);
      row.classList.add('drop-' + zone);
    });
    tree.addEventListener('dragleave', (e) => { if (!tree.contains(e.relatedTarget)) clearDropClasses(); });
    tree.addEventListener('drop', async (e) => {
      const row = e.target.closest('.dr-row[data-id]');
      if (!row || !dragging) return;
      e.preventDefault();
      const target = byId(row.dataset.id);
      const zone = dragging.type === 'doc' ? 'inside' : zoneFor(e, row);
      const drag = dragging;
      clearDropClasses();
      if (drag.type === 'doc') { moveDocs([drag.id], target.id, {}); return; }
      try {
        if (zone === 'inside') await moveFolder(drag.id, target.id, null);
        else if (zone === 'before') await moveFolder(drag.id, target.parent_id, target.id);
        else await moveFolder(drag.id, target.parent_id, siblingAfter(target));
      } catch (err) { /* toast already shown */ }
    });

    document.querySelectorAll('[data-drop-folder]').forEach((el) => {
      el.addEventListener('dragover', (e) => { if (dragging && dragging.type === 'doc') { e.preventDefault(); el.classList.add('drop-inside'); } });
      el.addEventListener('dragleave', () => el.classList.remove('drop-inside'));
      el.addEventListener('drop', (e) => {
        if (!dragging || dragging.type !== 'doc') return;
        e.preventDefault();
        moveDocs([dragging.id], Number(el.dataset.dropFolder), {});
      });
    });

    const drop = document.getElementById('dr-drop');
    if (drop) {
      drop.addEventListener('dragover', (e) => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); drop.classList.add('over'); } });
      drop.addEventListener('dragleave', () => drop.classList.remove('over'));
      drop.addEventListener('drop', (e) => {
        if (!e.dataTransfer.files.length) return;
        e.preventDefault();
        drop.classList.remove('over');
        uploadFiles(e.dataTransfer.files, drop.dataset.folderId);
      });
    }
  }

  // ------------------------------------------------------------ boot
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closePopover(); return; }
    const tag = (document.activeElement && document.activeElement.tagName) || '';
    if (/INPUT|TEXTAREA|SELECT/.test(tag) || e.metaKey || e.ctrlKey || e.altKey) return;
    if (!document.getElementById('dr-tree')) return;
    if (e.key === 'n' || e.key === 'N') { e.preventDefault(); newFolder(state.selectedId || null); }
    if (e.key === 'F2' && state.selectedId) { e.preventDefault(); renameFolder(state.selectedId); }
  });

  document.addEventListener('DOMContentLoaded', () => {
    try {
      const raw = sessionStorage.getItem('drToast');
      if (raw) { sessionStorage.removeItem('drToast'); renderToast(JSON.parse(raw)); }
      const rename = sessionStorage.getItem('drRename');
      if (rename) { sessionStorage.removeItem('drRename'); renameFolder(Number(rename)); }
    } catch (e) { /* ignore */ }
    setupDnD();
    initPicker();
  });

  return {
    setVisibility, toggleVisibility, bulkVisibility, newFolder, renameFolder, deleteFolder, bulkDelete, bulkMove,
    moveFolderPicker, folderMenu, docMenu, pickFolder, moveDocs, selectedDocIds, onDocSelect, toggleAllDocs,
    clearDocSelection, onFolderSelect, uploadFiles, confirmDeleteDocs, toggleService, chooseScratch, createStructure, startBlank, setMaster, setContactAccess,
    openSharePanel, notify,
  };
})();
