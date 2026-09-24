"""Data Room helpers shared by the staff routes and the client portal.

A Data Room is a client's curated, folder-organised view of documents that
already exist in Documents. Filing a document only sets Document.folder_id.
A folder is visible to the client only if it and every ancestor is
visible_to_client, so that rule lives here once instead of in each route.
"""
from collections import defaultdict

from sqlalchemy import func

from extensions import db
from models import DataRoomFolder, Document

# A Data Room is built from the services we deliver to the client. Each
# service brings top-level folders (name -> subfolders); folders that several
# services share are created once with their subfolders merged.
SERVICES = {
    "ma": {
        "name": "M&A and Fundraise",
        "desc": "Sell-side, buy-side and capital raise. A diligence-ready room for buyers and investors.",
        "folders": {
            "Corporate and Legal": ["Incorporation", "Shareholder Agreements"],
            "Financials": ["Historical Financials", "Financial Model"],
            "Commercial": [],
            "HR and IP": [],
            "Deal Documents": [],
        },
    },
    "cfo": {
        "name": "CFO Services",
        "desc": "Outsourced finance: monthly MIS, budgets, cash forecasts and audit support.",
        "folders": {
            "Financials": ["MIS Packs", "Budgets and Forecasts", "Cash Flow", "Audit and Accounts"],
            "Board Reporting": [],
        },
    },
    "comp": {
        "name": "Compliance",
        "desc": "Statutory calendar, GST and tax, company law and sector regulators such as RERA.",
        "folders": {
            "Compliance": ["Calendar", "GST and Tax", "Company Law", "Sector Regulatory", "Notices and Audit"],
        },
    },
    "adv": {
        "name": "Advisory",
        "desc": "Diagnostics, debt and investor work, strategy and board support.",
        "folders": {
            "Advisory": ["Diagnostic", "Debt and Investors", "Strategy and Plans"],
            "Board Reporting": [],
        },
    },
}
SERVICE_ORDER = ["ma", "cfo", "comp", "adv"]

# Top-level order in a finished room; Engagement is always first and the
# hidden working-papers folder always last.
TOP_ORDER = [
    "Corporate and Legal", "Financials", "Compliance", "Commercial", "HR and IP",
    "Advisory", "Deal Documents", "Board Reporting",
]
ENGAGEMENT = "Engagement"
WORKING_PAPERS = "Advisor Working Papers"

# Words in a client's engagement type that pre-tick a service.
SERVICE_HINTS = {
    "ma": ("m&a", "fundrais", "investment banking", "capital raise"),
    "cfo": ("cfo", "finance"),
    "comp": ("compliance", "regulatory"),
    "adv": ("advisory", "advisor"),
}


def guess_services(engagement_type):
    text = (engagement_type or "").lower()
    return [k for k in SERVICE_ORDER if any(h in text for h in SERVICE_HINTS[k])]


def build_structure(keys):
    """(name, subfolders, visible_to_client) for the chosen services, merged."""
    keys = [k for k in SERVICE_ORDER if k in set(keys)]
    if not keys:
        return []
    merged = {}
    for k in keys:
        for name, subs in SERVICES[k]["folders"].items():
            bucket = merged.setdefault(name, [])
            bucket.extend(x for x in subs if x not in bucket)
    out = [(ENGAGEMENT, [], True)]
    for name in TOP_ORDER:
        if name in merged:
            out.append((name, merged[name], True))
    out.append((WORKING_PAPERS, [], False))
    return out


class Tree:
    def __init__(self, client_id):
        self.client_id = client_id
        folders = (
            DataRoomFolder.query.filter_by(client_id=client_id, deleted_at=None)
            .order_by(DataRoomFolder.position, DataRoomFolder.id).all()
        )
        self.by_id = {f.id: f for f in folders}
        self.children = defaultdict(list)
        for f in folders:
            parent = f.parent_id if f.parent_id in self.by_id else None
            self.children[parent].append(f)
        rows = (
            db.session.query(Document.folder_id, func.count(Document.id))
            .filter(Document.client_id == client_id, Document.folder_id.isnot(None))
            .group_by(Document.folder_id).all()
        )
        self.direct = {fid: n for fid, n in rows if fid in self.by_id}

    def parent_of(self, folder):
        return self.by_id.get(folder.parent_id)

    def path(self, folder_id):
        chain, seen = [], set()
        f = self.by_id.get(folder_id)
        while f and f.id not in seen:
            seen.add(f.id)
            chain.append(f)
            f = self.by_id.get(f.parent_id)
        return list(reversed(chain))

    def effective_visible(self, folder):
        return all(f.visible_to_client for f in self.path(folder.id))

    def hidden_by_ancestor(self, folder):
        parent = self.parent_of(folder)
        return bool(parent) and not self.effective_visible(parent)

    def descendants(self, folder_id):
        out, stack = set(), [folder_id]
        while stack:
            current = stack.pop()
            for child in self.children.get(current, []):
                if child.id not in out:
                    out.add(child.id)
                    stack.append(child.id)
        return out

    def doc_count(self, folder_id, include_children=True):
        ids = {folder_id} | (self.descendants(folder_id) if include_children else set())
        return sum(self.direct.get(i, 0) for i in ids)

    def flat(self):
        """[(folder, depth)] in display order."""
        out = []

        def walk(parent_id, depth):
            for f in self.children.get(parent_id, []):
                out.append((f, depth))
                walk(f.id, depth + 1)

        walk(None, 0)
        return out

    def visible_ids(self):
        return {f.id for f in self.by_id.values() if self.effective_visible(f)}

    def counts(self):
        total = len(self.by_id)
        return len(self.visible_ids()), total

    def next_position(self, parent_id):
        siblings = self.children.get(parent_id, [])
        return (max((s.position for s in siblings), default=-1) + 1)


def can_contact_open(client, contact):
    return bool(
        client.data_room_enabled and contact.portal_access and contact.data_room_access
    )


def visible_documents(client_id, tree):
    ids = tree.visible_ids()
    if not ids:
        return []
    return (
        Document.query.filter(Document.client_id == client_id, Document.folder_id.in_(ids))
        .order_by(Document.uploaded_at.desc()).all()
    )


def apply_template(client_id, keys):
    """Create the folders for the chosen services. Safe to run again to add
    services later: existing folders are kept, only missing ones (and missing
    subfolders of existing ones) are created."""
    tree = Tree(client_id)
    tops = {f.name.lower(): f for f in tree.by_id.values() if f.parent_id is None}
    position = tree.next_position(None)
    created = 0
    for name, subfolders, visible in build_structure(keys):
        parent = tops.get(name.lower())
        if parent is None:
            parent = DataRoomFolder(client_id=client_id, name=name, visible_to_client=visible, position=position)
            db.session.add(parent)
            db.session.flush()
            position += 1
            created += 1
            have = set()
            sub_pos = 0
        else:
            have = {f.name.lower() for f in tree.children.get(parent.id, [])}
            sub_pos = tree.next_position(parent.id)
        for sub in subfolders:
            if sub.lower() in have:
                continue
            db.session.add(DataRoomFolder(client_id=client_id, parent_id=parent.id, name=sub, position=sub_pos))
            sub_pos += 1
            created += 1
    return created


def build_client_view(client_id, tree, folder_id=None, q=""):
    """Everything the client-facing Data Room page needs, restricted to what a
    client may see. Shared by the portal and the staff 'Preview as client'."""
    visible = tree.visible_ids()
    current = tree.by_id.get(folder_id) if folder_id in visible else None
    crumbs = tree.path(current.id) if current else []

    def files_in(fid):
        ids = ({fid} | tree.descendants(fid)) & visible
        return sum(tree.direct.get(i, 0) for i in ids)

    parent_id = current.id if current else None
    subfolders = [
        {"folder": f, "files": files_in(f.id),
         "folders": sum(1 for c in tree.children.get(f.id, []) if c.id in visible)}
        for f in tree.children.get(parent_id, []) if f.id in visible
    ]

    all_docs = visible_documents(client_id, tree)
    q = (q or "").strip()
    if q:
        docs, mode = [d for d in all_docs if q.lower() in d.file_name.lower()], "search"
    elif current:
        docs, mode = [d for d in all_docs if d.folder_id == current.id], "folder"
    else:
        docs, mode = all_docs[:8], "recent"

    labels = {d.id: " / ".join(f.name for f in tree.path(d.folder_id)) for d in docs}
    updated = max((d.uploaded_at for d in all_docs), default=None)
    return {
        "current": current, "crumbs": crumbs, "subfolders": subfolders, "docs": docs,
        "mode": mode, "q": q, "labels": labels, "updated": updated,
        "has_folders": bool(visible), "total_files": len(all_docs),
    }
