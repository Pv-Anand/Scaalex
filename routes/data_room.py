from datetime import datetime

from flask import Blueprint, render_template, request, jsonify, url_for
from flask_login import login_required, current_user

from extensions import db
from models import ClientContact, DataRoomFolder, Document, log_activity
from routes.clients import get_client_or_404
from routes.documents import save_upload
from data_room import Tree, TEMPLATES, apply_template, build_client_view

data_room_bp = Blueprint("data_room", __name__, url_prefix="/clients/<slug>/data-room")


def _fail(message, status=400):
    return jsonify(ok=False, error=message), status


def _body():
    return request.get_json(silent=True) or {}


def _folder(client, folder_id):
    return DataRoomFolder.query.filter_by(id=folder_id, client_id=client.id, deleted_at=None).first()


def _clean_name(raw):
    name = " ".join((raw or "").split())
    return name[:200]


def _name_taken(client, parent_id, name, ignore_id=None):
    q = DataRoomFolder.query.filter(
        DataRoomFolder.client_id == client.id, DataRoomFolder.deleted_at.is_(None),
        DataRoomFolder.parent_id == parent_id, db.func.lower(DataRoomFolder.name) == name.lower(),
    )
    if ignore_id:
        q = q.filter(DataRoomFolder.id != ignore_id)
    return q.first() is not None


def _folder_json(folder, tree):
    return {
        "id": folder.id, "name": folder.name, "parent_id": folder.parent_id,
        "visible": bool(folder.visible_to_client),
        "hidden_by_ancestor": tree.hidden_by_ancestor(folder),
        "doc_count": tree.doc_count(folder.id),
    }


def _log(client, action, folder=None, details=None):
    log_activity(
        current_user.id, client.id, action, "data_room_folder" if folder else None,
        folder.id if folder else None, details=details or (folder.name if folder else None),
    )


# ---------------------------------------------------------------- page

@data_room_bp.route("")
@login_required
def page(slug):
    client = get_client_or_404(slug)
    tree = Tree(client.id)

    folder_id = request.args.get("folder", type=int)
    selected = tree.by_id.get(folder_id) if folder_id else None
    preview = request.args.get("preview") == "1"

    if selected:
        docs = Document.query.filter_by(client_id=client.id, folder_id=selected.id).order_by(Document.uploaded_at.desc()).all()
        subfolders = tree.children.get(selected.id, [])
    else:
        docs = []
        subfolders = tree.children.get(None, [])

    all_docs = client.documents.all()
    unfiled = sum(1 for d in all_docs if d.folder_id not in tree.by_id)
    visible_count, total_count = tree.counts()
    contacts = client.contacts.filter_by(portal_access=True).all()
    view = build_client_view(client.id, tree, folder_id, request.args.get("q", "")) if preview else None

    return render_template(
        "data_room.html", client=client, active_tab="documents", tree=tree,
        selected=selected, docs=docs, subfolders=subfolders, preview=preview,
        unfiled_count=unfiled, filed_count=len(all_docs) - unfiled, all_count=len(all_docs),
        visible_count=visible_count, total_count=total_count,
        preview_contact=next((c for c in contacts if c.data_room_access), None),
        view=view, link=lambda fid=None: url_for("data_room.page", slug=client.slug, preview=1, folder=fid),
        search_action=url_for("data_room.page", slug=client.slug), search_hidden={"preview": "1"},
        download_url=lambda d: url_for("documents.download", doc_id=d.id),
    )


# ---------------------------------------------------------------- folders

@data_room_bp.route("/folders", methods=["POST"])
@login_required
def create_folder(slug):
    client = get_client_or_404(slug)
    data = _body()
    name = _clean_name(data.get("name"))
    if not name:
        return _fail("Give the folder a name.")
    parent_id = data.get("parent_id") or None
    if parent_id and not _folder(client, parent_id):
        return _fail("That parent folder no longer exists.", 404)
    if _name_taken(client, parent_id, name):
        return _fail(f'A folder called "{name}" already exists here.', 409)

    tree = Tree(client.id)
    folder = DataRoomFolder(client_id=client.id, parent_id=parent_id, name=name, position=tree.next_position(parent_id))
    db.session.add(folder)
    db.session.flush()
    _log(client, "Data Room folder created", folder)
    db.session.commit()
    return jsonify(ok=True, folder=_folder_json(folder, Tree(client.id)))


@data_room_bp.route("/folders/template", methods=["POST"])
@login_required
def use_template(slug):
    client = get_client_or_404(slug)
    key = _body().get("template")
    if key == "blank":
        return jsonify(ok=True, created=0)
    if key not in TEMPLATES:
        return _fail("Unknown template.")
    created = apply_template(client.id, key)
    _log(client, "Data Room structure created", None, details=f"{key}: {created} folders")
    db.session.commit()
    return jsonify(ok=True, created=created)


@data_room_bp.route("/folders/<int:folder_id>/rename", methods=["POST"])
@login_required
def rename_folder(slug, folder_id):
    client = get_client_or_404(slug)
    folder = _folder(client, folder_id)
    if not folder:
        return _fail("Folder not found.", 404)
    name = _clean_name(_body().get("name"))
    if not name:
        return _fail("Give the folder a name.")
    if _name_taken(client, folder.parent_id, name, ignore_id=folder.id):
        return _fail(f'A folder called "{name}" already exists here.', 409)
    old = folder.name
    folder.name = name
    _log(client, "Data Room folder renamed", folder, details=f"{old} -> {name}")
    db.session.commit()
    return jsonify(ok=True, folder=_folder_json(folder, Tree(client.id)), previous=old)


@data_room_bp.route("/folders/<int:folder_id>/visibility", methods=["POST"])
@login_required
def set_visibility(slug, folder_id):
    client = get_client_or_404(slug)
    folder = _folder(client, folder_id)
    if not folder:
        return _fail("Folder not found.", 404)
    visible = bool(_body().get("visible"))
    tree = Tree(client.id)
    if visible and tree.hidden_by_ancestor(folder):
        parent = tree.parent_of(folder)
        return _fail(f'Show "{parent.name}" first to share this folder.', 409)
    folder.visible_to_client = visible
    _log(client, "Data Room folder shown to client" if visible else "Data Room folder hidden from client", folder)
    db.session.commit()
    return jsonify(ok=True, folder=_folder_json(folder, Tree(client.id)))


@data_room_bp.route("/folders/bulk-visibility", methods=["POST"])
@login_required
def bulk_visibility(slug):
    client = get_client_or_404(slug)
    data = _body()
    visible = bool(data.get("visible"))
    ids = [int(i) for i in data.get("folder_ids", []) if str(i).isdigit()]
    changed = []
    for folder_id in ids:
        folder = _folder(client, folder_id)
        if folder and bool(folder.visible_to_client) != visible:
            folder.visible_to_client = visible
            changed.append(folder.id)
    if changed:
        _log(client, "Data Room folder shown to client" if visible else "Data Room folder hidden from client",
             None, details=f"{len(changed)} folders")
    db.session.commit()
    return jsonify(ok=True, changed=changed)


@data_room_bp.route("/folders/<int:folder_id>/move", methods=["POST"])
@login_required
def move_folder(slug, folder_id):
    client = get_client_or_404(slug)
    folder = _folder(client, folder_id)
    if not folder:
        return _fail("Folder not found.", 404)
    data = _body()
    parent_id = data.get("parent_id") or None
    before_id = data.get("before_id") or None

    tree = Tree(client.id)
    if parent_id:
        if parent_id == folder.id or parent_id in tree.descendants(folder.id):
            return _fail("A folder cannot be moved inside itself.", 409)
        if parent_id not in tree.by_id:
            return _fail("Destination folder not found.", 404)
    if parent_id != folder.parent_id and _name_taken(client, parent_id, folder.name):
        return _fail(f'"{folder.name}" already exists in that folder.', 409)

    previous = {"parent_id": folder.parent_id, "before_id": None}
    old_siblings = [f for f in tree.children.get(folder.parent_id, [])]
    for i, s in enumerate(old_siblings):
        if s.id == folder.id and i + 1 < len(old_siblings):
            previous["before_id"] = old_siblings[i + 1].id

    siblings = [f for f in tree.children.get(parent_id, []) if f.id != folder.id]
    index = len(siblings)
    if before_id:
        for i, s in enumerate(siblings):
            if s.id == before_id:
                index = i
                break
    siblings.insert(index, folder)
    folder.parent_id = parent_id
    for i, s in enumerate(siblings):
        s.position = i
    _log(client, "Data Room folder moved", folder)
    db.session.commit()
    return jsonify(ok=True, previous=previous)


@data_room_bp.route("/folders/<int:folder_id>/delete", methods=["POST"])
@login_required
def delete_folder(slug, folder_id):
    client = get_client_or_404(slug)
    folder = _folder(client, folder_id)
    if not folder:
        return _fail("Folder not found.", 404)
    tree = Tree(client.id)
    ids = {folder.id} | tree.descendants(folder.id)
    docs = Document.query.filter(Document.client_id == client.id, Document.folder_id.in_(ids)).all()
    doc_map = {d.id: d.folder_id for d in docs}
    for d in docs:
        d.folder_id = None
    now = datetime.utcnow()
    for fid in ids:
        tree.by_id[fid].deleted_at = now
    _log(client, "Data Room folder deleted", folder, details=f"{folder.name} ({len(docs)} documents unfiled)")
    db.session.commit()
    return jsonify(ok=True, undo={"folder_ids": sorted(ids), "doc_map": doc_map}, doc_count=len(docs), name=folder.name)


@data_room_bp.route("/restore", methods=["POST"])
@login_required
def restore(slug):
    client = get_client_or_404(slug)
    data = _body()
    ids = {int(i) for i in data.get("folder_ids", [])}
    folders = DataRoomFolder.query.filter(
        DataRoomFolder.client_id == client.id, DataRoomFolder.id.in_(ids), DataRoomFolder.deleted_at.isnot(None),
    ).all()
    for f in folders:
        f.deleted_at = None
    valid_ids = {f.id for f in folders}
    doc_map = {int(k): int(v) for k, v in (data.get("doc_map") or {}).items()}
    if doc_map:
        for d in Document.query.filter(Document.client_id == client.id, Document.id.in_(list(doc_map))).all():
            if doc_map[d.id] in valid_ids:
                d.folder_id = doc_map[d.id]
    if folders:
        log_activity(current_user.id, client.id, "Data Room folder restored", "data_room_folder", folders[0].id,
                     details=f"{len(folders)} folders")
    db.session.commit()
    return jsonify(ok=True, restored=len(folders))


# ---------------------------------------------------------------- documents

@data_room_bp.route("/documents/move", methods=["POST"])
@login_required
def move_documents(slug):
    client = get_client_or_404(slug)
    data = _body()
    folder_id = data.get("folder_id") or None
    folder = None
    if folder_id:
        folder = _folder(client, folder_id)
        if not folder:
            return _fail("Folder not found.", 404)
    ids = [int(i) for i in data.get("doc_ids", []) if str(i).isdigit()]
    docs = Document.query.filter(Document.client_id == client.id, Document.id.in_(ids)).all()
    previous = {d.id: d.folder_id for d in docs}
    for d in docs:
        d.folder_id = folder.id if folder else None
    if docs:
        target = folder.name if folder else "Not filed"
        log_activity(current_user.id, client.id, "Documents filed to Data Room" if folder else "Documents removed from Data Room",
                     "data_room_folder" if folder else None, folder.id if folder else None,
                     details=f"{len(docs)} document{'s' if len(docs) != 1 else ''} -> {target}")
    db.session.commit()
    return jsonify(ok=True, moved=len(docs), previous=previous)


@data_room_bp.route("/upload", methods=["POST"])
@login_required
def upload(slug):
    client = get_client_or_404(slug)
    folder_id = request.form.get("folder_id", type=int)
    if folder_id and not _folder(client, folder_id):
        return _fail("Folder not found.", 404)
    files = request.files.getlist("files")
    saved, errors = 0, []
    for f in files:
        try:
            save_upload(client, f, folder_id=folder_id)
            saved += 1
        except ValueError as exc:
            errors.append(f"{f.filename}: {exc}")
    db.session.commit()
    if not saved and errors:
        return _fail(errors[0])
    return jsonify(ok=True, saved=saved, errors=errors)


# ---------------------------------------------------------------- access

@data_room_bp.route("/master", methods=["POST"])
@login_required
def set_master(slug):
    client = get_client_or_404(slug)
    enabled = bool(_body().get("enabled"))
    client.data_room_enabled = enabled
    _log(client, "Data Room turned on" if enabled else "Data Room turned off", None, details=client.name)
    db.session.commit()
    return jsonify(ok=True, enabled=enabled)


@data_room_bp.route("/access/<int:contact_id>", methods=["POST"])
@login_required
def set_contact_access(slug, contact_id):
    client = get_client_or_404(slug)
    contact = ClientContact.query.filter_by(id=contact_id, client_id=client.id).first_or_404()
    enabled = bool(_body().get("enabled"))
    if enabled and not contact.portal_access:
        return _fail("Turn on Portal Access first.", 409)
    contact.data_room_access = enabled
    log_activity(
        current_user.id, client.id, "Data Room access granted" if enabled else "Data Room access revoked",
        "client_contact", contact.id, details=contact.name,
    )
    db.session.commit()
    return jsonify(ok=True, enabled=enabled, name=contact.name)
