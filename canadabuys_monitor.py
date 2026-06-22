#!/usr/bin/env python3
"""
CanadaBuys IT opportunity monitor.

Pulls the official Government of Canada "Open tender notices" feed from CanadaBuys,
filters to IT / informatics opportunities, flags the ones realistically doable with
an AI assistant like Claude, extracts technical + business requirements, and writes
an HTML + Markdown + CSV report. Designed to run unattended as a GitHub Actions job.

Data source (Open Government Licence - Canada):
  https://canadabuys.canada.ca/opendata/pub/openTenderNotice-ouvertAvisAppelOffres.csv

Environment variables (all optional):
  DAYS_BACK            Only include notices published within this many days (default: 1)
  CANADABUYS_LOCAL_CSV Path to a local CSV instead of downloading (for testing)
  OUTPUT_DIR           Where to write reports (default: ./reports)
  MAX_DESC_CHARS       Truncate long descriptions in the report (default: 1200)
"""

from __future__ import annotations

import csv
import io
import os
import re
import sys
import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

OPEN_TENDERS_URL = (
    "https://canadabuys.canada.ca/opendata/pub/"
    "openTenderNotice-ouvertAvisAppelOffres.csv"
)

# --------------------------------------------------------------------------- #
# Keyword dictionaries
# --------------------------------------------------------------------------- #

# Terms that indicate an IT / informatics opportunity.
IT_KEYWORDS = [
    "information technology", "informatics", "software", "application",
    "cloud", "saas", "paas", "iaas", "database", "data analytics", "analytics",
    "data management", "data migration", "cyber", "cybersecurity",
    "network", "server", "computer", "digital", "website", "web portal",
    "web application", "system integration", "systems integration", "api ",
    "machine learning", "artificial intelligence", "data science",
    "geographic information system", " gis ", "erp", "crm", "devops",
    "programming", "developer", "development services", "it services",
    "it support", "service desk", "help desk", "helpdesk", "it security",
    "information management", " im/it", "im / it", "telecommunication",
    "data centre", "data center", "dashboard", "business intelligence",
    "etl", "automation", "robotic process", "modernization of",
    "software licence", "software license", "licensing", "platform",
]

# UNSPSC family prefixes that map to IT goods/services.
IT_UNSPSC_PREFIXES = ("43", "8111")

# GSIN prefixes commonly used for IT/telecom on CanadaBuys.
# D = Information processing & related telecom services; N70/N71 = ADP equip/software.
IT_GSIN_PREFIXES = ("D", "N70", "N71", "7010", "7030", "7035", "7042")

# Positive signals: work an AI assistant can substantially help deliver remotely.
CLAUDE_POSITIVE = [
    "research", "report", "analysis", "analyses", "documentation", "document ",
    "writing", "drafting", "content", "translation", "literature review",
    "data analysis", "data cleaning", "data entry", "summariz", "summaris",
    "policy analysis", "evaluation", "review of", "plain language",
    "spreadsheet", "dataset", "data visualization", "data visualisation",
    "requirements gathering", "business analysis", "needs assessment",
    "environmental scan", "knowledge synthesis", "comparative analysis",
    "develop a strategy", "framework", "guidance", "best practices",
    "prototype", "proof of concept", "script", "data extraction",
    "categoriz", "classification", "transcription", "metadata",
    "user guide", "training material", "communications material",
]

# Negative signals: work that needs people on site, clearances, or physical goods.
CLAUDE_NEGATIVE = [
    "on-site", "on site", "onsite", "security clearance", "secret clearance",
    "reliability status", "must be located", "physical", "installation",
    "cabling", "hardware supply", "supply and deliver", "supply of",
    "maintenance of equipment", "staff augmentation", "secondment",
    "resource augmentation", "per diem", "full-time on", "in-person",
    "construction", "furniture", "equipment rental", "field work",
    "travel required", "deployment to", "manned",
]

# Words used to classify a requirement sentence as technical vs business.
TECHNICAL_HINTS = [
    "shall", "must support", "compatible", "integrat", "architecture",
    "platform", "software", "hardware", "api", "database", "data ", "cloud",
    "security", "encryption", "accessibility", "wcag", "performance",
    "availability", "uptime", "interface", "format", "protocol", "system",
    "technical", "functionality", "feature", "configuration", "migration",
    "scalab", "interoperab", "standard", "version", "browser", "network",
]
BUSINESS_HINTS = [
    "deliverable", "milestone", "timeline", "schedule", "budget", "cost",
    "price", "payment", "warranty", "experience", "qualification",
    "mandatory criteria", "rated criteria", "evaluation", "bid", "proposal",
    "contract", "term", "option year", "deadline", "closing", "submit",
    "insurance", "licence", "certification", "reference", "resource",
    "personnel", "team", "bilingual", "official languages", "set-aside",
    "indigenous", "small business", "trade agreement", "value",
]


# --------------------------------------------------------------------------- #
# Column resolution (defensive: matches bilingual headers by substring)
# --------------------------------------------------------------------------- #

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def find_col(headers: list[str], *needles: str, prefer: str = "eng",
             exclude: tuple[str, ...] = ()) -> Optional[str]:
    """Return the first header whose normalized name contains all needles
    (and none of the excluded substrings). Prefers an English ('-eng')
    variant when several match."""
    needles_n = [_norm(n) for n in needles]
    excl_n = [_norm(e) for e in exclude]
    candidates = [h for h in headers
                  if all(n in _norm(h) for n in needles_n)
                  and not any(e in _norm(h) for e in excl_n)]
    if not candidates:
        return None
    for c in candidates:
        if prefer in c.lower():
            return c
    return candidates[0]


def match_kw(text: str, kw: str) -> bool:
    """Keyword match that respects word starts to avoid false hits like
    'erp' inside 'enterprise' or 'script' inside 'descriptions'.
    Keywords padded with spaces are matched as literal substrings."""
    if kw != kw.strip():            # caller added spaces as explicit boundaries
        return kw in text
    return re.search(r"\b" + re.escape(kw), text) is not None


@dataclass
class Fields:
    title: Optional[str] = None
    description: Optional[str] = None
    gsin: Optional[str] = None
    gsin_desc: Optional[str] = None
    unspsc: Optional[str] = None
    category: Optional[str] = None
    notice_type: Optional[str] = None
    status: Optional[str] = None
    pub_date: Optional[str] = None
    close_date: Optional[str] = None
    regions_delivery: Optional[str] = None
    trade: Optional[str] = None
    entity: Optional[str] = None
    end_users: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    notice_url: Optional[str] = None
    attachments: Optional[str] = None
    reference: Optional[str] = None
    solicitation: Optional[str] = None
    proc_method: Optional[str] = None


def resolve_fields(headers: list[str]) -> Fields:
    f = Fields()
    f.title = find_col(headers, "title")
    f.description = find_col(headers, "tenderdescription") or find_col(headers, "description", "appel")
    f.gsin = find_col(headers, "gsin", exclude=("description",))
    f.gsin_desc = find_col(headers, "gsindescription")
    f.unspsc = find_col(headers, "unspsc")
    f.category = find_col(headers, "procurementcategory")
    f.notice_type = find_col(headers, "noticetype")
    f.status = find_col(headers, "tenderstatus")
    f.pub_date = find_col(headers, "publicationdate")
    f.close_date = find_col(headers, "tenderclosingdate") or find_col(headers, "closingdate")
    f.regions_delivery = find_col(headers, "regionsofdelivery") or find_col(headers, "regionslivraison")
    f.trade = find_col(headers, "tradeagreements")
    f.entity = find_col(headers, "contractingentityname")
    f.end_users = find_col(headers, "enduserentitiesname") or find_col(headers, "enduser", "name")
    f.contact_name = find_col(headers, "contactinfoname")
    f.contact_email = find_col(headers, "contactinfoemail")
    f.notice_url = find_col(headers, "noticeurl")
    f.attachments = find_col(headers, "attachment")
    f.reference = find_col(headers, "referencenumber")
    f.solicitation = find_col(headers, "solicitationnumber")
    f.proc_method = find_col(headers, "procurementmethod")
    return f


# --------------------------------------------------------------------------- #
# Opportunity model + classification
# --------------------------------------------------------------------------- #

@dataclass
class Opportunity:
    row: dict
    f: Fields
    it_reasons: list[str] = field(default_factory=list)
    claude_tier: str = ""
    claude_score: int = 0
    claude_signals: list[str] = field(default_factory=list)
    technical_reqs: list[str] = field(default_factory=list)
    business_reqs: list[str] = field(default_factory=list)

    def g(self, col: Optional[str]) -> str:
        return (self.row.get(col, "") or "").strip() if col else ""

    @property
    def title(self) -> str:
        return self.g(self.f.title) or "(untitled notice)"

    @property
    def description(self) -> str:
        return self.g(self.f.description)

    @property
    def haystack(self) -> str:
        parts = [self.g(self.f.title), self.g(self.f.description),
                 self.g(self.f.gsin_desc)]
        return " ".join(parts).lower()


def is_it(opp: Opportunity) -> bool:
    reasons = []
    hay = opp.haystack
    hits = [k.strip() for k in IT_KEYWORDS if match_kw(hay, k)]
    if hits:
        reasons.append("keywords: " + ", ".join(sorted(set(hits))[:6]))
    unspsc = opp.g(opp.f.unspsc)
    if unspsc and unspsc.lstrip().startswith(IT_UNSPSC_PREFIXES):
        reasons.append(f"UNSPSC {unspsc[:4]}…")
    gsin = opp.g(opp.f.gsin).upper()
    if gsin and gsin.startswith(IT_GSIN_PREFIXES):
        reasons.append(f"GSIN {gsin}")
    opp.it_reasons = reasons
    return bool(reasons)


def score_claude(opp: Opportunity) -> None:
    hay = opp.haystack
    pos = sorted({k.strip() for k in CLAUDE_POSITIVE if match_kw(hay, k)})
    neg = sorted({k.strip() for k in CLAUDE_NEGATIVE if match_kw(hay, k)})
    score = len(pos) - len(neg)
    opp.claude_score = score
    opp.claude_signals = (["+ " + p for p in pos] + ["- " + n for n in neg])
    if len(pos) >= 2 and len(neg) == 0:
        opp.claude_tier = "Strong fit"
    elif len(pos) >= 1 and score >= 1:
        opp.claude_tier = "Possible fit"
    elif len(pos) >= 1:
        opp.claude_tier = "Possible fit (with caveats)"
    else:
        opp.claude_tier = "Unlikely"


_SENT_SPLIT = re.compile(r"(?<=[.;:])\s+|\n+|•|•|\s-\s")


def extract_requirements(opp: Opportunity) -> None:
    text = opp.description
    if not text:
        return
    sentences = [s.strip(" \t-•*") for s in _SENT_SPLIT.split(text)]
    sentences = [s for s in sentences if len(s) > 12]
    for s in sentences:
        low = s.lower()
        tech = sum(1 for h in TECHNICAL_HINTS if h in low)
        biz = sum(1 for h in BUSINESS_HINTS if h in low)
        if tech == 0 and biz == 0:
            continue
        if tech >= biz:
            opp.technical_reqs.append(s)
        else:
            opp.business_reqs.append(s)
    # De-dupe while preserving order, cap length
    opp.technical_reqs = list(dict.fromkeys(opp.technical_reqs))[:12]
    opp.business_reqs = list(dict.fromkeys(opp.business_reqs))[:12]


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #

def load_rows() -> tuple[list[dict], list[str]]:
    local = os.environ.get("CANADABUYS_LOCAL_CSV")
    if local:
        with open(local, "r", encoding="utf-8-sig", newline="") as fh:
            text = fh.read()
    else:
        import requests  # imported lazily so --help works without the dep
        resp = requests.get(OPEN_TENDERS_URL, timeout=120)
        resp.raise_for_status()
        resp.encoding = "utf-8-sig"
        text = resp.text
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    headers = reader.fieldnames or []
    return rows, headers


def parse_date(s: str) -> Optional[dt.date]:
    if not s:
        return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #

def build(days_back: int) -> tuple[list[Opportunity], Fields, dict]:
    rows, headers = load_rows()
    f = resolve_fields(headers)
    today = dt.date.today()
    cutoff = today - dt.timedelta(days=days_back)

    stats = {"total_rows": len(rows), "it_total": 0, "in_window": 0}
    opps: list[Opportunity] = []
    for row in rows:
        opp = Opportunity(row=row, f=f)
        if not is_it(opp):
            continue
        stats["it_total"] += 1
        pub = parse_date(opp.g(f.pub_date))
        if days_back >= 0 and pub is not None and pub < cutoff:
            continue
        stats["in_window"] += 1
        score_claude(opp)
        extract_requirements(opp)
        opps.append(opp)

    tier_rank = {"Strong fit": 0, "Possible fit": 1,
                 "Possible fit (with caveats)": 2, "Unlikely": 3}
    opps.sort(key=lambda o: (tier_rank.get(o.claude_tier, 9),
                             parse_date(o.g(f.close_date)) or dt.date.max))
    return opps, f, stats


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_html(opps, f, stats, days_back) -> str:
    maxc = int(os.environ.get("MAX_DESC_CHARS", "1200"))
    today = dt.date.today().isoformat()
    strong = sum(1 for o in opps if o.claude_tier == "Strong fit")
    possible = sum(1 for o in opps if o.claude_tier.startswith("Possible"))

    badge = {
        "Strong fit": "#0a7d28", "Possible fit": "#b8860b",
        "Possible fit (with caveats)": "#b8860b", "Unlikely": "#777",
    }
    cards = []
    for o in opps:
        url = o.g(f.notice_url)
        title_html = f'<a href="{_esc(url)}">{_esc(o.title)}</a>' if url else _esc(o.title)
        desc = o.description
        if len(desc) > maxc:
            desc = desc[:maxc].rsplit(" ", 1)[0] + " …"
        tech = "".join(f"<li>{_esc(t)}</li>" for t in o.technical_reqs) or "<li><em>See full notice / attachments.</em></li>"
        biz = "".join(f"<li>{_esc(b)}</li>" for b in o.business_reqs) or "<li><em>See full notice / attachments.</em></li>"
        attach = o.g(f.attachments)
        meta = []
        for label, val in [
            ("Closes", o.g(f.close_date)), ("Published", o.g(f.pub_date)),
            ("Buyer", o.g(f.entity)), ("GSIN", o.g(f.gsin)),
            ("UNSPSC", o.g(f.unspsc)), ("Category", o.g(f.category)),
            ("Notice type", o.g(f.notice_type)), ("Delivery", o.g(f.regions_delivery)),
            ("Contact", o.g(f.contact_email) or o.g(f.contact_name)),
            ("Reference", o.g(f.reference)),
        ]:
            if val:
                meta.append(f"<tr><td class='k'>{label}</td><td>{_esc(val)}</td></tr>")
        signals = ", ".join(_esc(s) for s in o.claude_signals) or "—"
        cards.append(f"""
        <div class="card">
          <div class="tier" style="background:{badge.get(o.claude_tier,'#777')}">{_esc(o.claude_tier)}</div>
          <h3>{title_html}</h3>
          <table class="meta">{''.join(meta)}</table>
          <p class="why"><strong>Why IT:</strong> {_esc('; '.join(o.it_reasons))}<br>
             <strong>Claude-fit signals:</strong> {signals}</p>
          <details open><summary>Technical requirements (extracted)</summary><ul>{tech}</ul></details>
          <details open><summary>Business requirements (extracted)</summary><ul>{biz}</ul></details>
          <details><summary>Summary / scope</summary><p>{_esc(desc) or '<em>No description provided.</em>'}</p></details>
          {f'<p class="attach"><strong>Attachments:</strong> {_esc(attach)}</p>' if attach else ''}
        </div>""")

    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1a1a1a;max-width:860px;margin:0 auto;padding:16px;}}
 h1{{font-size:20px;margin-bottom:2px}} .sub{{color:#555;font-size:13px;margin-top:0}}
 .summary{{background:#f3f6fb;border:1px solid #d8e0ee;border-radius:8px;padding:10px 14px;font-size:14px}}
 .card{{border:1px solid #e2e2e2;border-radius:10px;padding:14px 16px;margin:14px 0;box-shadow:0 1px 2px rgba(0,0,0,.04)}}
 .card h3{{margin:6px 0 8px;font-size:16px}} .card a{{color:#1155cc;text-decoration:none}}
 .tier{{display:inline-block;color:#fff;font-size:11px;font-weight:700;padding:2px 8px;border-radius:10px;letter-spacing:.3px}}
 table.meta{{border-collapse:collapse;font-size:13px;margin:4px 0}} table.meta td{{padding:1px 8px 1px 0;vertical-align:top}}
 td.k{{color:#666;white-space:nowrap;font-weight:600}}
 .why{{font-size:12.5px;color:#444;background:#fafafa;border-left:3px solid #ccc;padding:6px 10px;margin:8px 0}}
 details{{margin:6px 0;font-size:13px}} summary{{cursor:pointer;font-weight:600;color:#333}}
 ul{{margin:6px 0 6px 0;padding-left:20px}} li{{margin:3px 0}}
 .attach{{font-size:12px;color:#555;word-break:break-all}}
 .foot{{color:#888;font-size:11px;margin-top:24px;border-top:1px solid #eee;padding-top:8px}}
</style></head><body>
<h1>Government of Canada — daily IT bid opportunities</h1>
<p class="sub">CanadaBuys open tender notices · generated {today}</p>
<div class="summary">
 <strong>{len(opps)}</strong> open IT opportunities published in the last {days_back} day(s).
 <strong>{strong}</strong> flagged a strong fit for Claude, <strong>{possible}</strong> a possible fit.<br>
 Scanned {stats['total_rows']:,} open notices · {stats['it_total']:,} IT-related total.
 Ordered by Claude-fit, then closing date.
</div>
{''.join(cards) if cards else '<p>No new IT opportunities in this window today.</p>'}
<p class="foot">Source: CanadaBuys open tender notices (Open Government Licence – Canada).
 Requirements shown are auto-extracted from the notice summary and are a starting point only —
 always confirm against the full notice and its attached solicitation documents before bidding.
 Claude-fit flags are heuristic.</p>
</body></html>"""


def render_markdown(opps, f, stats, days_back) -> str:
    today = dt.date.today().isoformat()
    out = [f"# Government of Canada — daily IT bid opportunities",
           f"_CanadaBuys open tender notices · generated {today}_\n",
           f"**{len(opps)}** open IT opportunities published in the last {days_back} day(s). "
           f"Scanned {stats['total_rows']:,} open notices ({stats['it_total']:,} IT-related total).\n"]
    for o in opps:
        url = o.g(f.notice_url)
        head = f"## {o.title}  —  _{o.claude_tier}_"
        out.append(head)
        if url:
            out.append(f"[Full notice]({url})")
        rows = []
        for label, val in [("Closes", o.g(f.close_date)), ("Published", o.g(f.pub_date)),
                           ("Buyer", o.g(f.entity)), ("GSIN", o.g(f.gsin)),
                           ("UNSPSC", o.g(f.unspsc)), ("Category", o.g(f.category)),
                           ("Contact", o.g(f.contact_email) or o.g(f.contact_name)),
                           ("Reference", o.g(f.reference))]:
            if val:
                rows.append(f"- **{label}:** {val}")
        out.extend(rows)
        out.append(f"- **Why IT:** {'; '.join(o.it_reasons)}")
        out.append(f"- **Claude-fit signals:** {', '.join(o.claude_signals) or '—'}")
        out.append("\n**Technical requirements (extracted):**")
        out.extend([f"  - {t}" for t in o.technical_reqs] or ["  - _See full notice / attachments._"])
        out.append("\n**Business requirements (extracted):**")
        out.extend([f"  - {b}" for b in o.business_reqs] or ["  - _See full notice / attachments._"])
        out.append("")
    out.append("\n---\n_Requirements are auto-extracted from the notice summary; confirm against "
               "the full solicitation. Claude-fit flags are heuristic._")
    return "\n".join(out)


def write_csv(path, opps, f):
    cols = ["claude_tier", "title", "close_date", "pub_date", "buyer", "gsin",
            "unspsc", "category", "contact_email", "notice_url", "why_it"]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for o in opps:
            w.writerow([o.claude_tier, o.title, o.g(f.close_date), o.g(f.pub_date),
                        o.g(f.entity), o.g(f.gsin), o.g(f.unspsc), o.g(f.category),
                        o.g(f.contact_email), o.g(f.notice_url), "; ".join(o.it_reasons)])


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> int:
    days_back = int(os.environ.get("DAYS_BACK", "1"))
    out_dir = os.environ.get("OUTPUT_DIR", "reports")
    os.makedirs(out_dir, exist_ok=True)

    opps, f, stats = build(days_back)
    today = dt.date.today().isoformat()

    html = render_html(opps, f, stats, days_back)
    md = render_markdown(opps, f, stats, days_back)
    html_path = os.path.join(out_dir, f"canadabuys_it_{today}.html")
    md_path = os.path.join(out_dir, f"canadabuys_it_{today}.md")
    csv_path = os.path.join(out_dir, f"canadabuys_it_{today}.csv")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md)
    write_csv(csv_path, opps, f)

    # Expose paths/counts to the GitHub Actions runner.
    summary = (f"{len(opps)} IT opportunities (last {days_back}d) · "
               f"{sum(1 for o in opps if o.claude_tier=='Strong fit')} strong Claude fits")
    print(summary)
    print(f"Wrote: {html_path}\n       {md_path}\n       {csv_path}")
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as fh:
            fh.write(f"html_path={html_path}\n")
            fh.write(f"md_path={md_path}\n")
            fh.write(f"csv_path={csv_path}\n")
            fh.write(f"count={len(opps)}\n")
            fh.write(f"summary={summary}\n")

    # Optional email (true SMTP send). Skipped automatically if secrets absent.
    try:
        from send_email import maybe_send
        maybe_send(subject=f"[CanadaBuys] {summary} — {today}",
                   html_body=html, attachments=[md_path, csv_path])
    except Exception as e:  # never fail the job on email problems
        print(f"[email] skipped/failed: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
