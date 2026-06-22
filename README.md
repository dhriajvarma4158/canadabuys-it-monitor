# CanadaBuys IT opportunity monitor

A daily GitHub Actions job that pulls the Government of Canada's official
**CanadaBuys** open-tender feed, filters it down to **IT / informatics**
opportunities, flags the ones realistically **doable with an AI assistant like
Claude**, extracts **technical and business requirements**, and emails you a
formatted report.

It runs entirely on a GitHub-hosted runner, so it needs no server of your own
and no paid data subscription. The data is the federal government's own open
data, published under the Open Government Licence – Canada.

---

## What it produces

Every run writes three files into `reports/`:

| File | Purpose |
|------|---------|
| `canadabuys_it_<date>.html` | Styled report — used as the email body and viewable on its own |
| `canadabuys_it_<date>.md`   | Same content in Markdown |
| `canadabuys_it_<date>.csv`  | Flat shortlist for sorting/tracking in a spreadsheet |

Each opportunity card shows the buyer, closing date, GSIN/UNSPSC codes, contact,
a link to the full notice, **why it was tagged as IT**, the **Claude-fit signals**,
and the **extracted technical + business requirements**.

Opportunities are ranked **Strong fit → Possible fit → Unlikely** for Claude, then
by closing date.

---

## How it works

```
CanadaBuys open-tenders CSV  ──►  filter to IT  ──►  flag Claude-fit  ──►  extract
   (downloaded each run)          (keywords +        (positive vs        requirements
                                   GSIN/UNSPSC)        negative signals)   (tech / business)
                                                                              │
                                            HTML + Markdown + CSV  ◄──────────┘
                                                     │
                                          email (optional) + workflow artifact
```

Source feed: `https://canadabuys.canada.ca/opendata/pub/openTenderNotice-ouvertAvisAppelOffres.csv`
(refreshed by CanadaBuys each morning, 07:00–08:30 UTC-0500).

---

## Setup (about 10 minutes)

### 1. Create the repository
Create a new GitHub repo and add these files (keep the folder structure):

```
canadabuys_monitor.py
send_email.py
requirements.txt
.github/workflows/daily-canadabuys-report.yml
README.md
```

### 2. Turn on Actions
On GitHub: **Settings → Actions → General →** allow workflows to run. The schedule
(`cron: "0 14 * * *"`, ~9–10am Eastern) is already set in the workflow. You can also
trigger it any time from the **Actions** tab via **Run workflow** (workflow_dispatch).

### 3. (Optional) Enable email
The report is always saved as a downloadable **artifact** on each run, so email is
optional. To also receive it by email, add these repository secrets under
**Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Example | Notes |
|--------|---------|-------|
| `SMTP_HOST` | `smtp.gmail.com` | Your mail provider's SMTP server |
| `SMTP_PORT` | `587` | 587 (STARTTLS) or 465 (SSL) |
| `SMTP_USER` | `you@gmail.com` | SMTP login |
| `SMTP_PASS` | *app password* | **Use an app password, never your real password** |
| `EMAIL_TO`  | `you@work.com` | Comma-separate multiple recipients |
| `EMAIL_FROM`| `you@gmail.com` | Optional; defaults to `SMTP_USER` |

> **Gmail:** turn on 2-step verification, then create an **App password**
> (Google Account → Security → App passwords) and use that 16-character value as
> `SMTP_PASS`. You enter it directly into GitHub — it is never stored in the code.

If the SMTP secrets are missing, the email step is skipped automatically and the
job still succeeds.

---

## Configuration knobs

Set as workflow env vars (or locally as environment variables):

| Variable | Default | Meaning |
|----------|---------|---------|
| `DAYS_BACK` | `1` | Only include notices published within this many days. Use a larger number for the first run or a weekly digest. Set to `-1` to include all currently-open IT notices regardless of date. |
| `MAX_DESC_CHARS` | `1200` | Truncate long notice summaries in the report. |
| `OUTPUT_DIR` | `reports` | Where the files are written. |
| `CANADABUYS_LOCAL_CSV` | *(unset)* | Path to a local CSV instead of downloading — used for testing. |

To change the cadence, edit the `cron` line in the workflow
(e.g. weekly Mondays = `0 14 * * 1`). Cron is in **UTC**.

---

## Run it locally

```bash
pip install -r requirements.txt
DAYS_BACK=7 python canadabuys_monitor.py      # pulls live data, writes ./reports/
```

Test without network using the included approach:

```bash
CANADABUYS_LOCAL_CSV=sample_open_tenders.csv DAYS_BACK=3 python canadabuys_monitor.py
```

---

## Tuning the filters

All keyword lists live at the top of `canadabuys_monitor.py`:

- `IT_KEYWORDS`, `IT_UNSPSC_PREFIXES`, `IT_GSIN_PREFIXES` — what counts as IT.
- `CLAUDE_POSITIVE` / `CLAUDE_NEGATIVE` — what makes something a good/bad Claude fit.
- `TECHNICAL_HINTS` / `BUSINESS_HINTS` — how requirement sentences are sorted.

Edit those lists to make the report stricter or broader.

---

## Important caveats

- **Requirements are auto-extracted from each notice's summary text.** The full,
  authoritative requirements live in the attached solicitation documents linked
  from each notice. Always confirm against the full notice before bidding.
- **Claude-fit flags are heuristic** — a keyword-based first pass to help you
  triage, not a judgment that the work can be fully automated.
- The feed covers **federal** notices only. Provincial/municipal tenders live on
  separate portals (MERX, BC Bid, etc.).
- Data © Government of Canada, reused under the
  [Open Government Licence – Canada](https://open.canada.ca/en/open-government-licence-canada).
