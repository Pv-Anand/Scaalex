"""Load six months of sample engagement data into the "Alfa Estate" demo client.

Alfa Estate is a fictional real-estate developer used to show prospects how a
CFO + Compliance + Advisory engagement runs in Scaalex: milestones on the
client timeline, weekly updates with decisions and action items, client
requests, a filed Data Room and a leadership overview. Dates are relative to
today, so the timeline always ends "now" with the last six months behind it.

Usage:
    python3 seed_alfa_estate.py            # creates the client (stops if it already has data)
    python3 seed_alfa_estate.py --reset    # wipes the existing Alfa client and reloads it

Nothing here touches any other client.
"""
import os
import sys
import uuid
from datetime import date, datetime, timedelta

from flask import current_app

from app import create_app
from extensions import db
from models import (
    User, Client, ClientContact, Conversation, Decision, ActionItem, Milestone,
    MilestoneRequest, Document, DataRoomFolder, AIOverview, AuditLog,
)
from routes.clients import slugify, purge_client

app = create_app()

NAME = "Alfa Estate"
TODAY = date.today()
START = TODAY - timedelta(days=182)


def day(n):
    return START + timedelta(days=n)


def at(n, hour=11, minute=0):
    return datetime.combine(day(n), datetime.min.time()).replace(hour=hour, minute=minute)


# ---------------------------------------------------------------- PDF/CSV files

def _pdf_escape(text):
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(title, subtitle, sections):
    """Tiny dependency-free PDF writer (Helvetica, A4, wrapped text)."""
    lines = [("title", title), ("sub", subtitle), ("gap", "")]
    for heading, items in sections:
        lines.append(("h", heading))
        for it in items:
            bullet = it.startswith("- ")
            text = it[2:] if bullet else it
            width = 92 if not bullet else 86
            words, cur = text.split(), ""
            first = True
            for w in words:
                if len(cur) + len(w) + 1 > width:
                    lines.append(("b" if bullet and first else ("bc" if bullet else "p"), cur))
                    cur, first = w, False
                else:
                    cur = (cur + " " + w).strip()
            lines.append(("b" if bullet and first else ("bc" if bullet else "p"), cur))
        lines.append(("gap", ""))
    lines.append(("foot", "Prepared by Scaalex for Alfa Estate Developers Pvt. Ltd. - Sample data for demonstration only"))

    pages, cur, y = [], [], 800
    for kind, text in lines:
        size = {"title": 20, "sub": 11, "h": 13}.get(kind, 10)
        lead = {"title": 30, "sub": 20, "h": 22, "gap": 8, "foot": 14}.get(kind, 14)
        if y - lead < 60:
            pages.append(cur)
            cur, y = [], 800
        y -= lead
        if kind != "gap":
            x = 56 + (14 if kind in ("b", "bc") else 0) + (12 if kind == "bc" else 0)
            if kind == "b":
                text = "-  " + text
                x -= 0
            font = "F2" if kind in ("title", "h") else "F1"
            cur.append(f"BT /{font} {size} Tf {x} {y} Td ({_pdf_escape(text)}) Tj ET")
    pages.append(cur)

    objs = []
    n_pages = len(pages)
    # 1 catalog, 2 pages, 3 F1, 4 F2, then page/content pairs
    objs.append("<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{5 + 2 * i} 0 R" for i in range(n_pages))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>")
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    for i, content in enumerate(pages):
        stream = "\n".join(content)
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {6 + 2 * i} 0 R >>"
        )
        objs.append(f"<< /Length {len(stream.encode('latin-1', 'replace'))} >>\nstream\n{stream}\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1", "replace")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def make_csv(rows):
    return "\n".join(",".join(str(c) for c in r) for r in rows).encode()


def mis_csv():
    rows = [["Month", "Sales bookings (INR Cr)", "Collections (INR Cr)", "Construction spend (INR Cr)",
             "Overheads (INR Cr)", "Net cash (INR Cr)", "Closing cash (INR Cr)"]]
    cash = 6.2
    data = [
        ("Apr", 9.1, 7.4, 5.9, 1.3), ("May", 10.4, 8.1, 6.2, 1.3), ("Jun", 11.8, 9.6, 6.8, 1.4),
        ("Jul", 12.6, 10.9, 7.1, 1.4), ("Aug", 13.9, 12.2, 7.4, 1.5), ("Sep", 14.7, 13.1, 7.6, 1.5),
    ]
    for m, s, c, k, o in data:
        net = round(c - k - o, 1)
        cash = round(cash + net, 1)
        rows.append([m, s, c, k, o, net, cash])
    return make_csv(rows)


def cashflow_csv():
    rows = [["Week", "Opening cash (INR Cr)", "Customer receipts", "Contractor payments", "Statutory dues",
             "Debt service", "Overheads", "Closing cash (INR Cr)"]]
    cash = 6.9
    receipts = [3.1, 3.4, 2.9, 3.6, 3.2, 3.8, 3.3, 3.5, 3.9, 3.4, 3.7, 4.0, 3.6]
    contractor = [1.9, 2.0, 1.8, 2.1, 1.9, 2.2, 1.9, 2.0, 2.3, 2.0, 2.1, 2.3, 2.0]
    statutory = [0.0, 0.4, 0.0, 0.0, 0.5, 0.0, 0.0, 0.4, 0.0, 0.0, 0.5, 0.0, 0.0]
    debt = [0.0, 0.0, 0.0, 1.1, 0.0, 0.0, 0.0, 1.1, 0.0, 0.0, 0.0, 1.1, 0.0]
    for i in range(13):
        close = round(cash + receipts[i] - contractor[i] - statutory[i] - debt[i] - 0.35, 2)
        rows.append([f"W{i + 1}", round(cash, 2), receipts[i], contractor[i], statutory[i], debt[i], 0.35, close])
        cash = close
    return make_csv(rows)


def compliance_csv():
    rows = [["Obligation", "Applies to", "Frequency", "Owner", "Next due", "Status"]]
    items = [
        ("RERA quarterly project update", "Alfa Heights, Alfa Business Park", "Quarterly", "Neha Kulkarni", 20, "On track"),
        ("RERA Form 3 CA certificate", "Alfa Heights", "Per withdrawal", "Scaalex", 12, "On track"),
        ("GSTR-1", "Company", "Monthly", "Neha Kulkarni", 6, "Filed"),
        ("GSTR-3B", "Company", "Monthly", "Neha Kulkarni", 9, "Filed"),
        ("GST input credit reconciliation (2A vs books)", "Company", "Monthly", "Scaalex", 14, "In progress"),
        ("TDS on contractor payments (26Q)", "Company", "Quarterly", "Neha Kulkarni", 25, "On track"),
        ("TDS on property purchases (Form 26QB follow-up)", "Buyers", "Per sale", "Scaalex", 10, "On track"),
        ("Advance tax instalment", "Company", "Quarterly", "Scaalex", 18, "On track"),
        ("MCA annual filings (AOC-4, MGT-7)", "Company", "Annual", "Company secretary", 45, "Scheduled"),
        ("Statutory audit sign-off", "Company", "Annual", "Auditor", 60, "Scheduled"),
    ]
    for o, a, f, ow, d, s in items:
        rows.append([o, a, f, ow, (TODAY + timedelta(days=d)).strftime("%d %b %Y"), s])
    return make_csv(rows)


# ---------------------------------------------------------------- the story

CONTACTS = [
    ("Rohit Malhotra", "rohit.malhotra@alfaestate.example", "+91 98200 10001", "Founder and Managing Director", True),
    ("Neha Kulkarni", "neha.kulkarni@alfaestate.example", "+91 98200 10002", "Finance Controller", False),
    ("Vikram Shah", "vikram.shah@alfaestate.example", "+91 98200 10003", "Head of Projects", False),
]

# key -> (title, status, done_day / due_day)
MILESTONES = [
    ("kickoff", "Engagement kick-off and scope sign-off", "completed", 5),
    ("diagnostic", "Finance and compliance diagnostic", "completed", 21),
    ("mis", "Monthly MIS and cash dashboard live", "completed", 45),
    ("pnl", "Project-wise P&L and budget-vs-actual framework", "completed", 62),
    ("rera", "RERA and GST compliance calendar and clean-up", "completed", 80),
    ("audit", "Statutory audit readiness review", "completed", 98),
    ("cash13", "13-week rolling cash flow forecast", "completed", 118),
    ("debt", "Debt restructuring and lender options note", "completed", 130),
    ("im", "Investor teaser and information memorandum", "completed", 150),
    ("dataroom", "Financial model and data room set up", "completed", 160),
    ("board", "First quarterly board reporting pack", "completed", 172),
    ("lenders", "Lender and investor meetings", "in_progress", 196),
    ("termsheet", "Term sheet negotiation support", "upcoming", 217),
    ("closing", "Closing checklist and conditions precedent tracker", "upcoming", 242),
    ("handover", "FY27 budget and MIS handover", "upcoming", 257),
]

# milestone key -> [(file name, ext, kind, folder path)]; kind "pdf:<key>" or csv builder name
DELIVERABLES = {
    "kickoff": [("Engagement Letter and Scope.pdf", "engagement_letter", "Engagement")],
    "diagnostic": [("Finance and Compliance Diagnostic Report.pdf", "diagnostic", "Advisory / Diagnostic")],
    "mis": [("MIS Pack - Sep.csv", "mis_csv", "Financials / MIS Packs")],
    "pnl": [("Project P&L Framework - Alfa Heights.pdf", "pnl", "Financials / Project Reporting")],
    "rera": [("Compliance Calendar and Tracker.csv", "compliance_csv", "Compliance / Calendar"),
             ("RERA and GST Clean-up Summary.pdf", "rera", "Compliance / RERA and GST")],
    "audit": [("Audit Readiness Checklist.pdf", "audit", "Compliance / Audit")],
    "cash13": [("13-Week Cash Flow Forecast.csv", "cashflow_csv", "Financials / Cash Flow"),
               ("Cash Flow Forecast Notes.pdf", "cash13", "Financials / Cash Flow")],
    "debt": [("Debt Restructuring and Lender Options Note.pdf", "debt", "Advisory / Debt and Investors")],
    "im": [("Investor Teaser - Alfa Estate.pdf", "teaser", "Advisory / Debt and Investors"),
           ("Information Memorandum Summary.pdf", "im", "Advisory / Debt and Investors")],
    "dataroom": [("Financial Model Assumptions.pdf", "model", "Advisory / Debt and Investors")],
    "board": [("Board Pack - Q1 FY27.pdf", "boardpack", "Board Reporting")],
}

PDF_CONTENT = {
    "engagement_letter": ("Engagement Letter and Scope", "Alfa Estate Developers Pvt. Ltd. - CFO, Compliance and Advisory Services", [
        ("Purpose", ["Scaalex will act as outsourced CFO, compliance lead and capital-raising adviser to Alfa Estate for an initial period of twelve months, with a six-month checkpoint."]),
        ("Scope of services", [
            "- CFO services: monthly MIS, project-wise P&L, cash flow forecasting, budgeting and board reporting.",
            "- Compliance: RERA, GST, TDS and MCA calendar, audit readiness and regulator correspondence.",
            "- Advisory: debt restructuring, lender and investor outreach, information memorandum and transaction support for a INR 60 Cr growth raise."]),
        ("Working rhythm", ["- Weekly working call with the Finance Controller and a fortnightly review with the founder.",
                            "- Monthly MIS by the 10th, quarterly board pack, and a live client portal for milestones and documents."]),
    ]),
    "diagnostic": ("Finance and Compliance Diagnostic Report", "Findings from the first three weeks of the engagement", [
        ("Headline", ["Alfa Estate is profitable at project level but reports late and manages cash by feel. Three risks need action before any capital raise."]),
        ("Key findings", [
            "- Month-end books closed 25 to 30 days late, so management sees results after decisions are made.",
            "- No project-wise view: overheads and construction spend are pooled, hiding that Alfa Meadows is margin-thin.",
            "- Blended cost of debt is about 14.5 percent on INR 46 Cr of borrowings across five lenders.",
            "- RERA quarterly updates for two projects were filed late and GST input credit shows about INR 38 lakh of unreconciled mismatch.",
            "- Customer collections are tracked in spreadsheets kept by the sales team, not in the ledger."]),
        ("Recommended sequence", ["1. Stand up monthly MIS and a cash dashboard.", "2. Clean the compliance backlog and set a calendar.",
                                  "3. Build a 13-week cash forecast, then restructure debt and prepare the raise."]),
    ]),
    "pnl": ("Project P&L Framework - Alfa Heights", "Budget-vs-actual template agreed with Finance and Projects", [
        ("Structure", ["- Revenue recognised on the percentage-of-completion basis, tied to engineer certified progress.",
                       "- Direct costs booked by project: land, approvals, construction, marketing and finance cost.",
                       "- Central overheads allocated on a fixed, agreed key reviewed each quarter."]),
        ("Alfa Heights snapshot", ["- 240 units, 68 percent sold, construction 54 percent complete.",
                                   "- Budgeted margin 22 percent, current tracking margin 20.4 percent because of steel and labour escalation.",
                                   "- Variance report goes to the founder on the 10th of each month."]),
    ]),
    "rera": ("RERA and GST Clean-up Summary", "Backlog closed and controls put in place", [
        ("What was fixed", ["- Both delayed RERA quarterly updates filed with explanation; no penalty notice received.",
                            "- GST input credit reconciliation completed back to April: INR 31 lakh recovered, INR 7 lakh written off with approval.",
                            "- TDS on contractor payments corrected for two quarters and challans matched."]),
        ("Controls now in place", ["- Compliance calendar with named owners and a 7-day early-warning list on the client portal.",
                                   "- Monthly 2A versus books reconciliation before GSTR-3B is filed.",
                                   "- RERA designated account movements reviewed before each Form 3 certificate."]),
    ]),
    "audit": ("Audit Readiness Checklist", "Statutory audit, FY26", [
        ("Status", ["- Fixed asset register reconciled to the ledger and physical verification signed off.",
                    "- Customer advances reconciled unit by unit with the sales register.",
                    "- Related party transactions listed and board approvals located.",
                    "- Contingent liabilities schedule reviewed with counsel.",
                    "- Open auditor queries: 3 of 41, all with owners and dates."]),
    ]),
    "cash13": ("13-Week Cash Flow Forecast - Notes", "Method and key assumptions", [
        ("Method", ["Direct-method forecast refreshed every Monday from bank balances, the collections schedule, contractor payment runs and statutory dues."]),
        ("Assumptions", ["- Collections follow the construction-linked payment plan with a 12 day average delay.",
                         "- Contractor payments held to 45 day terms; no retention release before next quarter.",
                         "- Minimum operating cash buffer of INR 4 Cr set by the founder."]),
        ("Insight", ["Weeks 6 and 11 show a thin buffer. Advancing the Alfa Heights milestone billing by one week removes the squeeze."]),
    ]),
    "debt": ("Debt Restructuring and Lender Options Note", "Options to reduce cost and extend tenor", [
        ("Current position", ["INR 46 Cr across five lenders, blended cost about 14.5 percent, several bullet maturities inside 18 months."]),
        ("Options", [
            "- Option A: consolidate into one construction finance facility with a scheduled bank. Indicative 11.5 to 12 percent.",
            "- Option B: NBFC structured facility for Phase 2 land and approvals. Indicative 13 to 14 percent, faster to close.",
            "- Option C: growth capital of INR 60 Cr from a credit fund or family office at project level."]),
        ("Recommendation", ["Pursue A and C in parallel. A saves roughly INR 1.2 Cr a year in interest and C funds Phase 2 without diluting the founder."]),
    ]),
    "teaser": ("Investor Teaser - Alfa Estate", "Residential and commercial development platform, Pune", [
        ("Snapshot", ["- Three projects, 1,100 units planned, INR 84 Cr revenue in FY26 with 21 percent EBITDA margin.",
                      "- 68 percent sold in the flagship project, Alfa Heights, ahead of the RERA timeline.",
                      "- Seeking INR 60 Cr to launch Phase 2 of Alfa Business Park."]),
    ]),
    "im": ("Information Memorandum Summary", "Confidential", [
        ("Contents", ["- Company overview and founder track record.", "- Project pipeline, approvals status and unit economics.",
                      "- Historical financials and the three-year plan.", "- Use of proceeds, security package and proposed governance."]),
        ("Investment highlights", ["- Micro-market with limited quality supply and rising absorption.", "- Escrow-style collections control under RERA.",
                                   "- Institutional-grade reporting since April: monthly MIS and a live 13-week cash forecast."]),
    ]),
    "model": ("Financial Model Assumptions", "Three-year project-level model", [
        ("Key assumptions", ["- Price growth 5 percent a year, absorption 14 units a month in Alfa Heights and 9 in Alfa Business Park.",
                             "- Construction cost inflation 6 percent, financing at 12 percent on the new facility.",
                             "- Base case equity IRR 22 percent, downside case 15 percent with a 6 month delay."]),
    ]),
    "boardpack": ("Board Pack - Q1 FY27", "First quarterly pack prepared under the new reporting format", [
        ("Dashboard", ["- Bookings INR 72.5 Cr year to date, collections INR 61.3 Cr, closing cash INR 9.4 Cr.",
                       "- Blended cost of debt trending down from 14.5 percent to 13.8 percent after two repayments.",
                       "- All statutory filings current; no notices open."]),
        ("Decisions requested", ["- Approve the lender shortlist and mandate.", "- Approve the Phase 2 launch budget subject to funding."]),
    ]),
}

FOLDERS = [  # path -> visible to client
    ("Engagement", True), ("Financials", True), ("Financials / MIS Packs", True), ("Financials / Project Reporting", True),
    ("Financials / Cash Flow", True), ("Compliance", True), ("Compliance / Calendar", True), ("Compliance / RERA and GST", True),
    ("Compliance / Audit", True), ("Advisory", True), ("Advisory / Diagnostic", True), ("Advisory / Debt and Investors", True),
    ("Board Reporting", True), ("Advisor Working Papers", False),
]

# (day, type, participants, notes, summary, context, questions, decisions, action items)
# decision: (text, context, owner, status); action: (task, owner, due_day, priority, status)
S = "Scaalex"
C = "Client"
UPDATES = [
    (2, "Meeting", ["Anand", "Rohit Malhotra", "Neha Kulkarni"],
     "Kick-off with the founder and finance team. Agreed the three workstreams, the weekly rhythm and who owns what. Rohit's goals: institutional-grade reporting, a clean compliance record and a INR 60 Cr raise for Phase 2 within nine months.",
     "Kick-off held. Three workstreams agreed (CFO, compliance, advisory) with a weekly working call and a fortnightly founder review.",
     "The founder wants to raise INR 60 Cr for Phase 2 within nine months, so reporting and compliance must be lender-ready first.",
     ["Which lenders does Alfa already have relationships with beyond the current five?"],
     [("Run the engagement in three workstreams with one shared milestone timeline", "Client portal will show progress to the founder.", "Rohit Malhotra", "Confirmed")],
     [("Send data request list to Finance", S, 3, "High", "Completed"), ("Share last two years' audited financials and project budgets", "Neha Kulkarni", 9, "High", "Completed")]),
    (4, "Video Call", ["Anand", "Neha Kulkarni"],
     "Walked through the data request list line by line. Neha flagged that collections sit in a sales-team spreadsheet and that bank reconciliations are monthly, not weekly.",
     "Data request walked through with Finance. Collections data is kept in a sales spreadsheet, not the ledger.",
     "Any forecast will start from the sales spreadsheet until collections are moved into the books.",
     [], [],
     [("Export the collections spreadsheet with unit-wise dues", "Neha Kulkarni", 8, "Medium", "Completed")]),
    (12, "Meeting", ["Anand", "Neha Kulkarni", "Vikram Shah"],
     "Working session on diagnostic findings. Projects team confirmed steel and labour costs are running above budget on Alfa Heights. No project-wise cost tracking exists today.",
     "Reviewed early diagnostic findings with Finance and Projects. Cost escalation confirmed on Alfa Heights and no project-wise cost view exists.",
     "Alfa Meadows is the margin-thin project and needs to be looked at separately.",
     ["What is the real cost to complete Alfa Heights after steel escalation?"],
     [],
     [("Share project cost-to-complete estimates for all three projects", "Vikram Shah", 18, "High", "Completed")]),
    (20, "Meeting", ["Anand", "Rohit Malhotra", "Neha Kulkarni"],
     "Diagnostic readout to the founder. Main messages: reporting is late, no project view, debt is expensive, and the compliance backlog is fixable in one quarter. Rohit agreed to fund a proper monthly close.",
     "Diagnostic report presented to the founder, who agreed to the sequence: MIS first, compliance clean-up, cash forecast, then debt and raise.",
     "Founder has accepted that no capital raise starts until three clean monthly MIS packs exist.",
     [],
     [("Sequence the work: MIS, compliance clean-up, cash forecast, then debt and raise", "Founder agreed after the diagnostic readout.", "Rohit Malhotra", "Confirmed"),
      ("Move to a 10-day monthly close", "Needed for credible MIS and later lender diligence.", "Neha Kulkarni", "Confirmed")],
     [("Finalise MIS format and chart of accounts mapping", S, 30, "High", "Completed"), ("Set up project codes in the accounting system", "Neha Kulkarni", 32, "Medium", "Completed")]),
    (30, "Video Call", ["Anand", "Neha Kulkarni"],
     "Reviewed the draft MIS layout. Agreed KPIs: bookings, collections, construction spend, cash and debt service. Neha will close books by the 10th starting next month.",
     "MIS layout and KPIs agreed. Books to be closed by the 10th from next month.",
     "First MIS will be a partial pack because project codes are new.",
     [], [],
     [("Build MIS template and cash dashboard", S, 40, "High", "Completed")]),
    (38, "Phone Call", ["Anand", "Rohit Malhotra", "Vikram Shah"],
     "Escalation on overdue customer instalments in Alfa Heights: 14 buyers are behind by more than 45 days, about INR 2.3 Cr. Agreed a structured follow-up rather than legal notices for now.",
     "Overdue instalments of about INR 2.3 Cr across 14 buyers in Alfa Heights. A structured follow-up plan agreed instead of legal notices.",
     "Collections speed directly drives the cash forecast and lender comfort.",
     [],
     [("Use a structured follow-up with escalation ladder before any legal notice", "Protects customer relationships while the project is still selling.", "Rohit Malhotra", "Confirmed")],
     [("Draft the buyer follow-up and escalation ladder", S, 42, "High", "Completed"), ("Call the 14 overdue buyers within a week", "Sales team", 46, "High", "Completed")]),
    (47, "Meeting", ["Anand", "Rohit Malhotra", "Neha Kulkarni"],
     "First monthly MIS review. Books closed on day 12, two days late but the first time within two weeks. Collections INR 7.4 Cr against bookings of INR 9.1 Cr. Overheads were 14 percent of collections.",
     "First MIS delivered on day 12. Collections INR 7.4 Cr against bookings INR 9.1 Cr; overheads at 14 percent of collections.",
     "Target is to close within 10 days and lift the collections-to-bookings ratio above 90 percent.",
     ["Can overheads be trimmed to 12 percent of collections by year end?"],
     [("Adopt the MIS as the single source for management and board discussions", "Replaces the ad hoc sales and finance spreadsheets.", "Rohit Malhotra", "Confirmed")],
     [("Retire the sales collections spreadsheet after next close", "Neha Kulkarni", 60, "Medium", "Completed")]),
    (58, "Internal Discussion", ["Anand", "Scaalex compliance team"],
     "Internal triage of compliance gaps. Priority order: RERA quarterly updates, GST 2A mismatch, TDS on contractor payments, then MCA filings. Estimated six weeks to clean fully.",
     "Internal triage of the compliance backlog completed with a six-week plan.",
     "GST mismatch of about INR 38 lakh is the biggest financial exposure.",
     [], [],
     [("Reconcile GST input credit back to April", S, 75, "High", "Completed"), ("File both delayed RERA quarterly updates", S, 70, "High", "Completed")]),
    (66, "Meeting", ["Anand", "Rohit Malhotra", "Neha Kulkarni", "Vikram Shah"],
     "Project P&L framework signed off. Alfa Heights tracking margin 20.4 percent against a 22 percent budget. Alfa Meadows margin at 9 percent, below the 14 percent threshold.",
     "Project P&L framework signed off. Alfa Heights margin tracking 20.4 percent; Alfa Meadows at 9 percent, below threshold.",
     "Alfa Meadows pricing and cost plan need a decision before any further land spend.",
     ["Should the remaining Alfa Meadows plots be repriced?"],
     [("Freeze new land spend on Alfa Meadows until pricing is reviewed", "Margin below the 14 percent minimum.", "Rohit Malhotra", "Confirmed"),
      ("Cost-to-complete to be reviewed monthly for all projects", "Agreed to catch escalation earlier.", "Vikram Shah", "Confirmed")],
     [("Prepare Alfa Meadows repricing options", S, 80, "Medium", "Completed")]),
    (78, "Video Call", ["Anand", "Neha Kulkarni"],
     "RERA updates filed and GST reconciliation nearly done: INR 31 lakh of input credit recovered, INR 7 lakh to be written off. Explained the new pre-filing checks.",
     "Both RERA updates filed. GST reconciliation recovers INR 31 lakh of credit; INR 7 lakh to be written off.",
     "A write-off approval is needed from the founder before the audit.",
     [],
     [("Write off INR 7 lakh of unrecoverable input credit", "Supplier invoices could not be matched or obtained.", "Rohit Malhotra", "Confirmed")],
     [("Circulate compliance calendar with owners", S, 84, "Medium", "Completed")]),
    (90, "Meeting", ["Anand", "Neha Kulkarni", "Statutory auditor"],
     "Audit readiness review with the statutory auditor. Fixed asset register and customer advances reconciled. 41 query points, 3 open.",
     "Audit readiness review held with the auditor. 38 of 41 queries closed.",
     "Auditor is comfortable with timelines provided the remaining three points close in two weeks.",
     [], [],
     [("Close the last three auditor queries", "Neha Kulkarni", 104, "High", "Completed")]),
    (101, "Email", ["Anand", "Statutory auditor"],
     "Auditor confirmed all queries closed and draft financials will be circulated next week. No qualifications expected.",
     "Auditor confirmed all queries closed. No qualifications expected.",
     "Clean audit strengthens the information memorandum.",
     [], [], []),
    (112, "Meeting", ["Anand", "Rohit Malhotra", "Neha Kulkarni"],
     "Walkthrough of the 13-week cash forecast. Buffer is thin in weeks 6 and 11. Advancing the Alfa Heights billing milestone by a week removes the squeeze.",
     "13-week cash forecast walked through. Weeks 6 and 11 are thin; advancing a billing milestone by a week fixes it.",
     "Minimum buffer of INR 4 Cr set by the founder.",
     ["Can the contractor payment run be moved from weekly to fortnightly?"],
     [("Refresh the cash forecast every Monday and share with the founder", "Ends surprises on cash.", "Neha Kulkarni", "Confirmed")],
     [("Ask the projects team to advance the Alfa Heights billing milestone", "Vikram Shah", 120, "High", "Completed")]),
    (124, "Video Call", ["Anand", "Rohit Malhotra"],
     "Lender options discussion: consolidate with a scheduled bank (about 11.5 to 12 percent) versus a faster NBFC facility. Rohit prefers to avoid dilution but is open to a credit fund for Phase 2.",
     "Lender options reviewed. Founder prefers debt over equity and is open to a credit fund for Phase 2.",
     "Bank consolidation saves roughly INR 1.2 Cr a year but takes 10 to 12 weeks.",
     [],
     [("Pursue bank consolidation and a Phase 2 credit fund in parallel", "Saves interest now and funds growth without dilution.", "Rohit Malhotra", "Confirmed")],
     [("Prepare lender shortlist and approach plan", S, 135, "High", "Completed")]),
    (134, "WhatsApp", ["Anand", "Rohit Malhotra"],
     "Quick update after two exploratory lender calls: one bank indicated 11.75 percent subject to clean audit; one NBFC indicated 13.5 percent with a faster close.",
     "Two exploratory lender calls: a bank at about 11.75 percent subject to clean audit and an NBFC at about 13.5 percent.",
     "Indicative terms only; nothing is committed.",
     [], [], []),
    (146, "Meeting", ["Anand", "Rohit Malhotra", "Neha Kulkarni"],
     "Investor narrative and information memorandum review. Positioned Alfa as an institutional-grade platform: clean audit, monthly MIS, live cash forecast, RERA current.",
     "Investor narrative and IM reviewed. Story is reporting discipline plus a strong flagship project.",
     "The founder's track record chapter needs one more project case study.",
     ["Which past project makes the best case study?"],
     [("Position Alfa as an institutional-grade platform in the teaser and IM", "Reporting discipline is the differentiator.", "Rohit Malhotra", "Confirmed")],
     [("Add a case study of the completed Alfa Residency project", "Rohit Malhotra", 152, "Medium", "Completed"), ("Finalise teaser and IM", S, 150, "High", "Completed")]),
    (158, "Video Call", ["Anand", "Neha Kulkarni"],
     "Reviewed the data room structure and the financial model. Model base case gives 22 percent equity IRR, downside 15 percent.",
     "Data room structure and financial model reviewed. Base case 22 percent IRR, downside 15 percent.",
     "Data room goes live to selected lenders after board approval.",
     [], [],
     [("Load audited financials, approvals and project documents into the data room", "Neha Kulkarni", 166, "High", "Completed")]),
    (166, "Meeting", ["Anand", "Rohit Malhotra"],
     "Board pack dry run. Bookings INR 72.5 Cr YTD, collections INR 61.3 Cr, closing cash INR 9.4 Cr, cost of debt down to 13.8 percent.",
     "Board pack dry run completed. Numbers agreed, two slides simplified for directors.",
     "Board will be asked to approve the lender shortlist and Phase 2 launch budget.",
     [], [],
     [("Send the board pack to directors 3 days before the meeting", S, 169, "High", "Completed")]),
    (172, "Meeting", ["Anand", "Board of Directors"],
     "Board meeting. Directors approved the lender shortlist and mandate for the INR 60 Cr raise. Phase 2 launch budget approved subject to funding.",
     "Board approved the lender shortlist, the INR 60 Cr raise mandate and the Phase 2 budget subject to funding.",
     "Now in the execution phase: lender and investor meetings begin.",
     [],
     [("Approve the lender shortlist and mandate for the INR 60 Cr raise", "Board resolution passed unanimously.", "Board of Directors", "Confirmed"),
      ("Approve Phase 2 launch budget subject to funding", "Conditional on financing close.", "Board of Directors", "Confirmed")],
     [("Circulate signed board resolutions", "Company secretary", 176, "Medium", "Completed"),
      ("Book first lender meetings", S, 180, "High", "Completed")]),
    (178, "Phone Call", ["Anand", "Rohit Malhotra"],
     "Prep for the first lender meetings. Agreed talking points, who joins, and the follow-up documents lenders will ask for: bank statements, sanction letters, RERA certificates.",
     "Preparation for lender meetings. Founder to lead the story, Scaalex to lead the numbers.",
     "Lenders will need three months of bank statements and current sanction letters.",
     ["Will the lead bank release its NOC for consolidation?"], [],
     [("Collect three months of bank statements for the lender pack", "Neha Kulkarni", 190, "High", "In Progress"),
      ("Request NOC from lead bank for consolidation", "Rohit Malhotra", 186, "High", "Waiting on Client"),
      ("Prepare lender Q and A document", S, 195, "Medium", "In Progress"),
      ("Draft investor update for the credit fund", S, 200, "Medium", "Not Started"),
      ("Review Alfa Meadows repricing decision", "Rohit Malhotra", 178, "Low", "Waiting on Client")]),
]

REQUESTS = [
    # milestone key, type, message, status, response text/doc
    ("pnl", "document", "Please share the latest project budget sheets for Alfa Heights and Alfa Business Park.", "received",
     ("Project Budget Sheets - Alfa Heights.pdf", "budget", 58)),
    ("audit", "document", "Please upload signed customer advance confirmations for the ten largest buyers.", "received",
     ("Customer Advance Confirmations.pdf", "advances", 88)),
    ("dataroom", "document", "Please upload the latest RERA registration certificates for all three projects.", "fulfilled",
     ("RERA Registration Certificates.pdf", "rera_certs", 168)),
    ("lenders", "document", "Please share the last three months of bank statements for all operating accounts.", "awaiting", None),
]

CLIENT_DOCS = {
    "budget": ("Project Budget Sheets", "Budget sheets supplied by the projects team", [
        ("Alfa Heights", ["- Total budget INR 142 Cr, spent INR 77 Cr, cost to complete INR 68 Cr (after escalation)."]),
        ("Alfa Business Park", ["- Total budget INR 96 Cr, spent INR 31 Cr, cost to complete INR 66 Cr."])]),
    "advances": ("Customer Advance Confirmations", "Signed confirmations, ten largest buyers", [
        ("Summary", ["- Ten confirmations totalling INR 18.4 Cr of advances, all matching the sales register."])]),
    "rera_certs": ("RERA Registration Certificates", "Alfa Heights, Alfa Business Park, Alfa Meadows", [
        ("Certificates", ["- Registration numbers and validity dates for all three projects, scanned copies attached in the original file."])]),
}


def wipe_existing(client):
    stored = purge_client(client)
    db.session.commit()
    folder = current_app.config["DOCUMENT_UPLOAD_FOLDER"]
    for name in stored:
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass


def build_file(kind):
    if kind == "mis_csv":
        return mis_csv()
    if kind == "cashflow_csv":
        return cashflow_csv()
    if kind == "compliance_csv":
        return compliance_csv()
    title, sub, sections = PDF_CONTENT.get(kind) or CLIENT_DOCS[kind]
    return make_pdf(title, sub, sections)


def store(client, file_name, kind, folder, milestone=None, when=None, user=None, contact=None):
    data = build_file(kind)
    ext = file_name.rsplit(".", 1)[-1].lower()
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(current_app.config["DOCUMENT_UPLOAD_FOLDER"], stored_name)
    with open(path, "wb") as fh:
        fh.write(data)
    doc = Document(
        client_id=client.id, milestone_id=milestone.id if milestone else None,
        folder_id=folder.id if folder else None, file_name=file_name, stored_name=stored_name,
        file_type=ext, file_size=len(data), uploaded_by_id=user.id if user else None,
        uploaded_by_contact_id=contact.id if contact else None, uploaded_at=when,
    )
    db.session.add(doc)
    db.session.flush()
    return doc


def seed(reset=False):
    with app.app_context():
        db.create_all()
        os.makedirs(current_app.config["DOCUMENT_UPLOAD_FOLDER"], exist_ok=True)

        matches = Client.query.filter(Client.name.ilike("alfa%")).all()
        if len(matches) > 1:
            print("More than one client starts with 'Alfa' - not touching anything:", [c.name for c in matches])
            return 1
        existing = matches[0] if matches else None
        if existing:
            has_data = (existing.conversations.count() + existing.milestones.count() + existing.documents.count()) > 0
            if has_data and not reset:
                print(f"'{existing.name}' already has data. Re-run with --reset to wipe and reload it.")
                return 1
            if reset:
                print(f"Wiping existing '{existing.name}' ...")
                wipe_existing(existing)
                existing = None

        users = User.query.order_by(User.id).all()
        if not users:
            print("No staff users found - create one first.")
            return 1
        owner = next((u for u in users if u.role == "owner"), users[0])
        managers = [u for u in users if u.role in ("owner", "admin")] or [owner]

        client = Client(
            name=NAME, slug=slugify(NAME), status="Active",
            engagement_type="CFO, Compliance and Advisory Services", is_demo=True,
            registered_name="Alfa Estate Developers Pvt. Ltd.",
            address="Alfa House, Baner Road, Pune 411045",
            website="www.alfaestate.example", gst_number="27AAAAA0000A1Z5",
            data_room_enabled=True, created_at=at(0, 9),
        )
        db.session.add(client)
        db.session.flush()
        client.portal_slug = f"alfa-estate-{TODAY.year}"

        contacts = {}
        for i, (name, email, phone, role, primary) in enumerate(CONTACTS):
            contact = ClientContact(
                client_id=client.id, name=name, email=email, phone=phone, designation=role,
                is_primary=primary, portal_access=False, data_room_access=True, created_at=at(1),
            )
            db.session.add(contact)
            contacts[name] = contact
        db.session.flush()

        # Data Room folders
        folders = {}
        for pos, (path, visible) in enumerate(FOLDERS):
            parts = [p.strip() for p in path.split("/")]
            parent = folders.get(" / ".join(parts[:-1])) if len(parts) > 1 else None
            f = DataRoomFolder(client_id=client.id, parent_id=parent.id if parent else None,
                               name=parts[-1], visible_to_client=visible, position=pos, created_at=at(6))
            db.session.add(f)
            db.session.flush()
            folders[path] = f

        # Milestones + deliverables
        milestones = {}
        for i, (key, title, status, offset) in enumerate(MILESTONES):
            m = Milestone(
                client_id=client.id, title=title, status=status,
                date=day(offset) if status == "completed" else None,
                due_date=day(offset - 3 if status == "completed" else offset) if status != "completed" else day(offset - 3),
                reporting_manager_id=managers[i % len(managers)].id, created_by_id=owner.id,
                visible_to_client=True, created_at=at(2),
            )
            if status != "completed":
                m.due_date = day(offset)
            db.session.add(m)
            db.session.flush()
            milestones[key] = m
            if status == "completed":
                db.session.add(AuditLog(user_id=managers[i % len(managers)].id, client_id=client.id,
                                        action="Milestone updated", entity_type="milestone", entity_id=m.id,
                                        details=f"{title} marked completed", created_at=at(offset, 16)))

        for key, files in DELIVERABLES.items():
            m = milestones[key]
            for file_name, kind, folder_path in files:
                doc = store(client, file_name, kind, folders[folder_path], milestone=m,
                            when=at(MILESTONES[[k for k, *_ in MILESTONES].index(key)][3], 15), user=owner)
                db.session.add(AuditLog(user_id=owner.id, client_id=client.id, action="Deliverable attached",
                                        entity_type="document", entity_id=doc.id, details=file_name,
                                        created_at=doc.uploaded_at))

        # A couple of internal working papers (hidden folder) and unfiled files
        store(client, "Lender Negotiation Notes (internal).pdf", "debt", folders["Advisor Working Papers"], when=at(140, 18), user=owner)
        store(client, "Diagnostic Working Notes (internal).pdf", "diagnostic", None, when=at(19, 18), user=owner)

        # Client requests and their responses
        for key, rtype, message, status, response in REQUESTS:
            m = milestones[key]
            offset = [k for k, *_ in MILESTONES].index(key)
            base = MILESTONES[offset][3]
            req = MilestoneRequest(
                milestone_id=m.id, request_type=rtype, message=message, status=status,
                requested_by_id=owner.id, requested_at=at(base - 12 if status != "awaiting" else 190, 10),
            )
            if response:
                file_name, kind, resp_day = response
                cdoc = store(client, file_name, kind, None, milestone=m, when=at(resp_day, 17),
                             contact=contacts["Neha Kulkarni"])
                req.response_document_id = cdoc.id
                req.fulfilled_by_contact_id = contacts["Neha Kulkarni"].id
                req.fulfilled_at = at(resp_day, 17)
                if status == "received":
                    req.received_by_id = owner.id
                    req.received_at = at(resp_day + 1, 10)
                db.session.add(AuditLog(client_contact_id=contacts["Neha Kulkarni"].id, client_id=client.id,
                                        action="Request fulfilled", entity_type="document", entity_id=cdoc.id,
                                        details=file_name, created_at=at(resp_day, 17)))
            db.session.add(req)
            db.session.add(AuditLog(user_id=owner.id, client_id=client.id, action="Requested from client",
                                    entity_type="milestone", entity_id=m.id, details=message,
                                    created_at=req.requested_at))

        # Updates, decisions, action items
        for (n, itype, people, notes, summary, context, questions, decisions, actions) in UPDATES:
            when = at(n, 11)
            conv = Conversation(
                client_id=client.id, interaction_type=itype, date=when, participants=people, raw_notes=notes,
                summary=summary, important_context=context, open_questions=questions,
                extraction_status="confirmed", created_by_id=owner.id, is_demo=True, created_at=when,
            )
            db.session.add(conv)
            db.session.flush()
            label = f"{itype} - {when.strftime('%d %b %Y')}"
            for text, ctx, dec_owner, dec_status in decisions:
                db.session.add(Decision(client_id=client.id, conversation_id=conv.id, decision=text, context=ctx,
                                        owner=dec_owner, status=dec_status, source_label=label, date=when, created_at=when))
            for task, a_owner, due, prio, status in actions:
                db.session.add(ActionItem(client_id=client.id, conversation_id=conv.id, task=task, owner=a_owner,
                                          due_date=day(due), priority=prio, status=status, source_label=label,
                                          created_at=when))
            db.session.add(AuditLog(user_id=owner.id, client_id=client.id, action="Update created",
                                    entity_type="conversation", entity_id=conv.id, details=summary[:120], created_at=when))

        # Portal sign-ins by the client team, so the Logs tab looks alive
        for n in (24, 47, 70, 96, 121, 148, 170, 179):
            db.session.add(AuditLog(client_contact_id=contacts["Rohit Malhotra"].id, client_id=client.id,
                                    action="Signed in", created_at=at(n, 19)))
            db.session.add(AuditLog(client_contact_id=contacts["Neha Kulkarni"].id, client_id=client.id,
                                    action="Signed in", created_at=at(n + 1, 10)))

        # Leadership overview
        db.session.add(AIOverview(
            client_id=client.id, generated_at=datetime.utcnow(), period_start=START, period_end=TODAY,
            executive_summary=(
                "Six months in, Alfa Estate has moved from late, spreadsheet-based reporting to a 10-day monthly close, "
                "a live 13-week cash forecast and a current compliance record. The board has approved the lender shortlist "
                "and mandate for a INR 60 Cr raise, and lender meetings are now under way. Cost of debt has fallen from "
                "14.5 to 13.8 percent and a bank consolidation could take it near 11.75 percent."),
            key_actions=[
                {"action": "Collect three months of bank statements for the lender pack", "owner": "Neha Kulkarni",
                 "due_date": day(190).isoformat(), "status": "In Progress"},
                {"action": "Obtain NOC from lead bank for consolidation", "owner": "Rohit Malhotra",
                 "due_date": day(186).isoformat(), "status": "Waiting on Client"},
                {"action": "Prepare lender Q and A document", "owner": "Scaalex",
                 "due_date": day(195).isoformat(), "status": "In Progress"}],
            key_decisions=[
                {"decision": "Approve lender shortlist and INR 60 Cr raise mandate", "date": day(172).isoformat(),
                 "context": "Board resolution passed unanimously.", "impact": "Unlocks lender and investor meetings."},
                {"decision": "Pursue bank consolidation and a Phase 2 credit fund in parallel", "date": day(124).isoformat(),
                 "context": "Founder prefers debt over dilution.", "impact": "Lowers interest cost and funds Phase 2."}],
            risks=[
                {"issue": "Lead bank NOC for consolidation is outstanding and gates the cheapest option.", "label": "Documented Fact"},
                {"issue": "Alfa Meadows margin of 9 percent remains below the 14 percent threshold.", "label": "Documented Fact"},
                {"issue": "Steel and labour escalation could erode Alfa Heights margin further.", "label": "Advisor Observation"}],
            leadership_notes=("Reporting and compliance are now lender-ready. The critical path is the lead bank NOC and the "
                              "Phase 2 credit fund conversation. Alfa Meadows needs a repricing decision."),
            suggested_next_steps=[
                "Chase the lead bank NOC through the founder's relationship manager.",
                "Send the information memorandum and data room access to the credit fund shortlist.",
                "Decide on Alfa Meadows repricing before Phase 2 launch."],
            changes_since_last="First overview for this engagement.", generated_by_id=owner.id,
        ))
        db.session.add(AuditLog(user_id=owner.id, client_id=client.id, action="Leadership overview generated",
                                entity_type="client", entity_id=client.id, created_at=datetime.utcnow()))

        db.session.commit()
        print(f"Loaded '{client.name}': {client.milestones.count()} milestones, {client.conversations.count()} updates, "
              f"{client.decisions.count()} decisions, {client.action_items.count()} action items, "
              f"{client.documents.count()} documents.")
        print(f"Staff page: /clients/{client.slug}   Portal: /client-login/{client.portal_slug}")
        return 0


if __name__ == "__main__":
    sys.exit(seed(reset="--reset" in sys.argv))
