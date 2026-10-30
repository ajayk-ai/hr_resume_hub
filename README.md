# hr-resume-hub

Loads the consolidated HR candidate sheet (`consol_hr_data`, Google Sheets) into PostgreSQL.
Each run replaces the target table in one transaction, so a failed run leaves the previous data intact.

## Layout

```
src/hr_resume_hub/
  config.py     settings from environment / .env, validated up front
  schema.py     sheet column -> database column mapping (single source of truth)
  parsers.py    cell text -> typed values (dates, experience, links)
  transform.py  rows -> records, counts values that could not be parsed
  sheets.py     Google Sheets reader, checks the header layout
  database.py   PostgreSQL writer (schema + table creation, truncate, COPY)
  pipeline.py   one run: read -> parse -> load
  cli.py        command-line entry point (hr-resume-hub)
  dashboard/    web dashboard for HR and HOD (hr-dashboard)
    app.py        Starlette app: auth, filters, sorting, stars
    repository.py SQL queries (filters are parameterised; sort keys are whitelisted)
    auth.py       scrypt password hashing, login throttling
    cli.py        serve, create-user, list-users, disable-user, enable-user
                  (app.py also serves the admin portal: /api/admin/users)
    static/       index.html, styles.css, app.js (no build step, no external CDN)
tests/          pytest suite (parsers, transform, schema, dashboard filters and auth)
```

## Setup

```powershell
uv sync
copy .env.example .env      # then fill in the values
```

Requires Python 3.14 and `service.json` (Google service account key) in the project root.
`.env` and `service.json` are git-ignored. Never commit them.

### Google access

Use one of these:

1. **Share the sheet** with the service account's email as Viewer. Leave `GOOGLE_DELEGATED_USER` unset.
2. **Domain-wide delegation.** Set `GOOGLE_DELEGATED_USER`. The Workspace admin must authorize the
   service account's client ID for the scope in `GOOGLE_SHEETS_SCOPE`. The scope must match exactly:
   authorizing `.../auth/spreadsheets` does not cover `.../auth/spreadsheets.readonly`.

### Configuration

| Variable | Required | Default | Notes |
|---|---|---|---|
| `GOOGLE_SERVICE_ACCOUNT_FILE` | yes | | Path relative to `.env` |
| `HR_SHEET_ID` | yes | | Legacy name `CONSOLATION_SHEET` also accepted |
| `GOOGLE_DELEGATED_USER` | no | | Only for domain-wide delegation |
| `GOOGLE_SHEETS_SCOPE` | no | `spreadsheets.readonly` | |
| `HR_DB_NAME` | yes | | Target database |
| `HR_DB_USER`, `HR_DB_PASSWORD` | yes | | Legacy `PO_DB_USER` / `PO_DB_PASSWORD` also accepted |
| `HR_DB_HOST`, `HR_DB_PORT` | no | `localhost`, `5432` | |
| `HR_DB_SCHEMA` | no | `resume_hub` | Created if missing |
| `DASHBOARD_SECRET_KEY` | dashboard | | 32+ random characters, signs session cookies |
| `DASHBOARD_HOST`, `DASHBOARD_PORT` | no | `127.0.0.1`, `8000` | |
| `DASHBOARD_HTTPS_ONLY` | no | `false` | Set `true` behind an HTTPS proxy |
| `DASHBOARD_SESSION_HOURS` | no | `8` | |

## Run

```powershell
uv run hr-resume-hub --dry-run   # read and parse only; nothing is written
uv run hr-resume-hub             # reload <schema>.candidates
```

Exit codes: `0` success, `1` unexpected failure, `2` configuration or sheet-layout error.

## Dashboard

A web page for HR and HODs to review candidates. Run the loader first, then:

```powershell
uv run hr-dashboard create-user --username admin --name "Dashboard Admin" --role admin   # first admin, prompts for a password
uv run hr-dashboard serve                                                                 # http://127.0.0.1:8000
```

Sign in as the admin to create every other account in the browser (see Admin portal below).
The command line still works for recovery: `create-user` on an existing username resets that user's
password and role. Other commands: `list-users`, `disable-user --username ...`, `enable-user --username ...`.

**Admin portal**

An `admin` account sees only the user administration page. It does not see candidate data.

- **Add user:** username, display name, role (`hr`, `hod` or `admin`) and password (at least 10 characters).
  "Generate" fills a random 14-character password and shows it so it can be copied. It is not shown again.
- **Edit:** change display name or role, or set a new password. Role changes and disabling take effect on the
  user's next request.
- **Disable / Enable:** users are disabled, never deleted, so existing stars keep their attribution.
- **Safeguard:** an admin cannot disable their own account or remove their own admin role, so at least one
  admin always remains.
- **Audit:** the server log records who created or changed which account. Passwords are never logged.

**What it does**

- **Filters:** free-text search, applied position, department, experience range, AI match score,
  received date range, and HR star / HOD star (Any, Starred, Not starred).
- **Position and department search:** type to search 3,000+ free-text values. Tick several, or press
  Enter / "Filter all N candidates matching ..." to add one chip that matches every variant containing
  all typed words in any order (`sap pp` matches `SAP PP / Production Planning`). Ticked values and
  "contains" chips are combined with OR.
- **Sorting:** click any column header. Sorting by HR or HOD star puts starred candidates first.
- **Stars:** an HR user stars as HR, an HOD user stars as HOD. Each role sees the other's star
  (with who starred and when) but cannot change it. HR users share one HR star per candidate, so any
  HR user can remove it. The first star wins and keeps its attribution.
- **Details:** click a row for contact details, AI summary, strengths, weaknesses and the resume link.
- **One row per candidate and position:** the same person applying twice for the same position shows
  once (the latest application). Identity is the email, else the phone, else the name.

**Stars survive data reloads.** They are stored in `candidate_stars`, keyed by `candidate_key` (a hash
of the candidate identity and position), not by row id. Do not change the rule in
`transform.candidate_key`: doing so would orphan existing stars.

**Security**

- Sign-in is required. Passwords are hashed with scrypt, five failed attempts lock a user and address
  for 15 minutes, and sessions last `DASHBOARD_SESSION_HOURS` (default 8).
- The server listens on `127.0.0.1` by default. To let others reach it, set `DASHBOARD_HOST`, put an
  HTTPS reverse proxy in front, and set `DASHBOARD_HTTPS_ONLY=true`. Without HTTPS, passwords and
  candidate data cross the network in clear text.
- The dashboard shows personal data (DPDPA). Create accounts only for people who need access, and
  disable accounts when people leave.
- Candidate text is rendered as plain text, never as HTML, and a Content-Security-Policy is sent.

## Development

```powershell
uv run pytest
uv run ruff check .
```

## Data notes

- Table `<schema>.candidates`: `id`, 28 sheet columns, `loaded_at`.
- `received_at` is `timestamptz`. Date-only values (`8/31/2026`, month/day/year) are stored as midnight IST.
- Experience columns are converted to years (`6 Years & 6 Months` becomes `6.50`).
- `notice_period` is kept as text because the sheet mixes `30 Days`, `15 Days or less` and `Serving Notice Period`.
- A value that has text but cannot be parsed is stored as NULL and reported as a warning with its count.
  Known cause: Google Sheets auto-converted some experience cells to dates (e.g. `1/2/1900`), which cannot
  be recovered. Format those columns as plain text in the source sheet.
- The `Received Time` sheet column duplicates `Received Date` and is not stored.
- The sheet's `Data Source` column (`Live`, `Last 3 Months`) is kept. Both sets are loaded, so check for
  overlapping candidates before reporting on counts.

## Data protection

The table holds candidate names, phone numbers, emails and salary details. This is personal data under
DPDPA. Restrict database access to HR and keep resume links and extracts internal.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `CERTIFICATE_VERIFY_FAILED ... self-signed certificate` | Corporate TLS inspection. `truststore` (loaded in `__init__.py`) fixes this. |
| `PermissionError` / HTTP 403 | Sheet is not shared with the service account |
| `unauthorized_client` | Delegation not enabled, or `GOOGLE_SHEETS_SCOPE` differs from the authorized scope |
| `Sheet header changed` | A column in the sheet was added, renamed or reordered; update `schema.py` |
