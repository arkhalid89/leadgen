# LeadGen

Find business leads from Google Maps and the open web, and fill in their contact
details with Google Gemini instead of crawling every website.

## How it works

A **search** is one job with up to three phases. Progress streams to the browser
over Server-Sent Events while it runs.

| Phase | Google Maps | Web Search |
| --- | --- | --- |
| **Discover** | Scroll the Maps result feed and read every card — name, rating, review count, category, street address, lat/lng and place id all come from the feed DOM, no page loads. Past ~90 leads the area is **geocoded and searched tile by tile**, because one Maps search stops yielding new results at ~100-120 however far you scroll. | Fan the keyword out into up to 8 query variants, run them through DuckDuckGo (or Serper.dev), keep one lead per registered domain, drop directories and social networks. |
| **Detail** | A pool of headless browsers (5 by default) opens listing pages *in parallel* for what the feed does not carry: **every phone number** the listing lists, and the canonical website. Skipped entirely in `fast` mode. | Not needed. |
| **Emails** | Leads that still have no email have their website read over plain async HTTP — homepage plus up to 3 contact-ish pages — for every published address, plus social links. No API key, no model. | Same. |

### Browser automation: Playwright

Scraping runs on **Playwright**, not Selenium. The honest summary of why:

* **Concurrency is a tab, not a process.** The Selenium version ran a pool of
  separate Chrome instances to parallelise detail pages. Playwright opens many
  pages inside one browser, so 8 concurrent readers cost 8 tabs rather than 8
  browser process trees. Measured: 8 concurrent pages added 11 processes and
  ~1.1 GB.
* **Auto-waiting** removes the explicit sleeps and polling loops the Selenium
  code needed, and with them a class of flaky timeouts.
* Images, fonts, media and stylesheets are aborted at the network layer rather
  than disabled through browser preferences, which is both more thorough and
  more reliable.

**On speed, be sceptical of the published 2x claims for this workload.** Those
compare naive implementations. The Selenium version here was already reading
most fields off the result feed and running detail pages in parallel, so most of
the available win had been taken. Measured on the same query (dentist, Austin
TX, 25 leads): Selenium 50.0s; Playwright 37.2s and 58.7s on two runs. The
run-to-run variance from Google itself is larger than the difference between the
drivers. Playwright was kept for the architecture, not a benchmark.

### Unlimited searches

A single Google Maps search returns roughly **100-120 results and then stops**,
whatever you do. Every serious Maps scraper answers this the same way -
geographic grid subdivision - and this one does too, adaptively.

Set the lead count to **∞ Unlimited** and the run:

1. geocodes the area through OpenStreetMap's Nominatim (free, no key),
2. splits its bounding box into a grid and searches each cell at its own zoom,
3. **splits any cell that comes back busy into four and searches those too** -
   a saturated cell is hiding more behind the per-search cap,
4. deduplicates everything by Google place id,
5. stops when the area is exhausted, the search ceiling is reached, or you stop
   it.

Cells are visited centre-outwards, because city centres are denser than
outskirts. A run ends early once four consecutive cells add nothing.

**Results are saved continuously**, so a long run can be stopped at any moment
without losing what it already found, and the leads are queryable while it is
still going.

Measured: "barber, Brighton UK" - a small city - passed 160 leads with 19 grid
areas still queued, against the ~120 ceiling a single search can ever reach.

### Coverage: why one search is not enough

A single Google Maps search returns roughly **100-120 results and then stops**,
no matter how far the feed is scrolled. That is Maps' own limit, not a scraping
one, and it is why asking for 500 restaurants in Milan used to return 107.

When the target exceeds `LEADGEN_GMAPS_SINGLE_SEARCH_YIELD` (90 by default) and
a location is given, the area is geocoded through OpenStreetMap's Nominatim
(free, no key) and its bounding box is split into a grid. Each cell gets its own
viewport search at `/maps/search/<keyword>/@<lat>,<lng>,15z`, and results are
deduplicated by place id.

Cells return largely *different* businesses — measured on Milan, three cells
returned 22 results each with 20-22 new every time. Searching Milan for
restaurants went from **107 to 230 leads** on the same request.

Cells are visited from the centre outwards, so if the lead limit cuts a run
short the densest areas are already covered. The run stops early when four
consecutive cells add nothing, which is what happens once a region is exhausted
or the grid wanders into farmland.

More cells means more coverage and more time: each is a fresh search plus
scrolling. Tune with `LEADGEN_GMAPS_MAX_CELLS` (default 25).

### Multiple emails and phones

A business often publishes several numbers and addresses, so leads store all of
them. `emails` and `phones` hold the full lists; `email` and `phone` hold the
primary one, so filters, sorting and single-cell exports keep working. The CSV
carries both — `Email`/`Phone` plus `All Emails`/`All Phones`. In the table the
primary is shown inline with a `+N` badge that expands the rest.

Phone numbers come from Maps, where each is its own `phone:tel:+…` entry, so
they are exact. Emails come from Gemini.

### Why emails are not sourced from an AI model

Finding a business's email is a **lookup**, not a reasoning task. A language
model with no live web access can only produce something email-shaped. Measured
against scraped ground truth, an ungrounded model returned `+1 512-454-0315` for
a business whose real number is `+1 512-877-9822`, a second model returned a
third, different wrong number, and both invented `info@<domain>` addresses the
prompt had explicitly forbidden them to construct.

Grounded (web-connected) models avoid this, but Google Search grounding has no
free quota — it returns HTTP 429 on an unbilled key, on every model.

The business's own website *is* the ground truth, and fetching it is free:

| | Website scrape | Ungrounded model | Grounded model |
| --- | --- | --- | --- |
| Cost | nothing | nothing | needs billing |
| Speed | ~0.4s/domain | ~1s/lead | ~3s/lead |
| Coverage | 40-60% | ~100% "answers" | ~70% |
| **Correct** | **yes, read off the site** | **no, invented** | yes |

A blank field means "not published", which is honest. An invented address means
bounced mail and a damaged sending reputation.

This is also not the slow crawling the previous version did. That drove a real
browser through up to eight pages per site, sequentially. This is plain async
HTTP — 25 sites in flight, a few pages each, no browser.

### Keeping the work down

Before any request is made:

1. Leads that already have an email are skipped.
2. Leads with no website are skipped.
3. Answers are cached by registered domain, so a site is fetched once — within a
   run and across runs.

Each run reports what it saved, e.g.
`Email lookup complete: 12 found from 12 sites (100%), 8 from cache, 0 skipped.`

### When the website gives nothing: search for the business by name

Two things stop a site giving up its address, and both are handled.

**The contact page is not where English sites keep it.** When a homepage is
JavaScript-rendered there are no links in the HTML to follow, so only guessed
paths are left — and a list of `/contact`, `/about`, `/impressum` simply 404s on
an Italian site. The guessed paths now cover Italian (`/contatti`, `/chi-siamo`,
`/prenota`), Spanish and Portuguese (`/contacto`, `/quienes-somos`, `/contato`),
French (`/nous-contacter`, `/a-propos`) and Dutch (`/over-ons`) as well.

**The site is unreachable, or the address is nowhere on it.** In that case the
business is looked up by name in a search engine, which frequently knows the
contact page we could not guess — real examples from one Milan run:
`brisketmilano.com/contatti/` and `gigi-gastronomia.it/contatti`. Those pages are
then fetched and read normally.

Addresses in the result snippets are used too, but **only when they plausibly
belong to that business**, because a page of search results mixes many
companies. Measured on one restaurant, blind harvesting returned three addresses
belonging to unrelated businesses. An address is accepted when it sits on the
business's own domain, or when its mailbox carries a distinctive word from the
business name — which keeps a chain's group address like
`ilcairoli@unacucina.it` while rejecting `info@bbqparadise.it` picked up from a
neighbouring result.

Anything found this way is stored with `email_source='search'`, so it is
distinguishable from an address read off the business's own site.

Searching is rate-limited by the engine, so it runs only for leads whose site
yielded nothing, at low concurrency, with a per-run budget
(`LEADGEN_EMAIL_SEARCH_MAX_LOOKUPS`, 150 by default).

Measured on 20 Milan restaurants that had produced **no email at all**: the
localised paths and search fallback together recovered **9 (45%)**, four of them
from the search step.

### Retrying the misses — "Find missing emails"

Sites time out, hosting is slow, DNS resolves only with `www`, front ends answer
403. The **Find missing emails** button on the Lead Database re-runs discovery
over every lead that has a website but no email, using the hardened fetcher:

* tries `https`, `https://www.`, bare host, and `http` variants
* accepts any 2xx (some front ends answer 202 for a perfectly good page)
* retries slow hosts once with a much longer timeout
* sends a full browser header set, which avoids many 403s
* retries once after a pause when DNS or the connection fails — those failures
  are frequently transient, and a second pass recovers most of them
* recognises anti-bot interstitials and does **not** harvest from them: one such
  page served `info@ninjamailtrap.com`, a spam trap planted to catch scrapers
* treats a Facebook or Instagram URL listed as the "website" as a social link
  rather than a site to crawl

It can optionally follow up with **AI suggestions** for whatever is still
missing, batched **20 businesses per request** to keep quota use low.

Read the numbers before switching that on. Against 12 businesses whose real
address had been read off their own website, the model scored **3 correct out of
10 answered — 30% precision**, and its failure mode is systematic: it answers
`info@<domain>` almost every time, a string you can build for free. So:

* suggestions never overwrite an address read from a website
* each is stored `email_source='ai'`, `email_status='unverified'`
* the domain's DNS **MX records are checked**; suggestions at domains that
  accept no mail are discarded
* the UI shows an `AI?` badge and the CSV carries `Email Source` and
  `Email Verified` columns
* if SMTP verification is on, prefer it: a confirmed `contact@` beats a guessed
  one every time

Treat AI output as a hypothesis to verify, never as a contact to mail blind.

### Where AI is genuinely useful

**Drafting outreach copy** — generative, no ground truth to get wrong. Optional:
without `GEMINI_API_KEY` the built-in templates are used.

### SMTP verification — the step that makes guessing worthwhile

`contact@devsinc.com` is a free hypothesis. Whether that mailbox *exists* can
only be answered by the domain's own mail server, and asking is also free. That
is what separates a scraped list from a usable contact database, and it is what
Hunter.io and Apollo are really selling.

Enable it with `LEADGEN_SMTP_VERIFY=1`. The **Find missing emails** dialog then
offers a verification pass that does two things:

**1. Confirms the addresses already found.** A site can advertise a mailbox that
was closed years ago. Each address is tested; ones the server rejects are
removed rather than left to bounce.

**2. Guesses and tests common mailboxes** for businesses that publish none —
`info@`, `contact@`, `hello@`, `admin@`, `sales@`, `support@`, `help@`, `hr@`
and so on, plus owner-derived forms (`sarah.malik@`, `sarah@`, `smalik@`) when
an owner name is known. **Only mailboxes the server accepts are kept.**

Per domain it resolves MX, opens one SMTP session, and reuses it for every
candidate. It never issues `DATA`, so **no mail is ever sent**.

Results carry a status, and the distinction matters:

| Status | Meaning |
| --- | --- |
| `verified` | The mail server accepted this recipient. Shown as a green ✓. |
| `invalid` | Rejected — the address is dropped. |
| `catch_all` | The domain accepts *every* address, so nothing can be concluded. Never treated as verified. |
| `unknown` | Greylisted or throttled. Reported honestly rather than guessed. |
| `no_mx` | The domain cannot receive mail at all. |

Catch-all detection is the important safeguard: before testing anything real,
a random mailbox that cannot exist is probed. If the server accepts *that*, every
answer from it is meaningless, and the whole domain is reported `catch_all`.

Measured on 60 real leads: of 34 scraped addresses, **15 verified, 7 catch-all,
7 unknown, 1 no-MX**. Pattern guessing then confirmed **3 more** mailboxes from
288 candidates across 24 domains — a smaller yield than scraping, but every one
is a confirmed mailbox rather than a 30% guess.

**Two practical notes.** Verification is slow by design: one connection per
domain, a short pause between probes, and bounded concurrency, because hammering
mail servers is how an IP gets rate-limited or blacklisted. And it needs outbound
port 25, which some networks and clouds (AWS/GCP by default) block — the
verifier tests this once, caches the answer, and reports `blocked` instead of
hanging on every domain.

Set `LEADGEN_SMTP_HELO_HOST` and `LEADGEN_SMTP_MAIL_FROM` to a domain you
actually control. Mail servers check them, and a bogus HELO is a fast way to get
blocked.

## Working the leads

Scraping is only half of it. What makes a list usable:

| | |
| --- | --- |
| **Lists** | Named, saved sets of leads. Colour-coded, exportable on their own. |
| **Tags** | Free-form labels, filterable, applied in bulk. |
| **Pipeline status** | new → contacted → replied → qualified → customer / rejected. |
| **Notes** | A timestamped activity trail per lead. |
| **Do-not-contact** | Addresses and domains excluded from **every** export. Unsubscribes, bounces, competitors. |
| **Saved searches** | Keep a search worth repeating and re-run it. |
| **Bulk actions** | Select rows, then tag, list, set status, suppress, export or delete in one go. |

Every lead opens into a detail drawer with its full contact record, all emails
and phones, socials, tags, lists and notes.

### Interaction

* **⌘K / Ctrl-K** opens a command palette: jump to any page, or find a lead by
  name, email or phone as you type.
* A running search shows in the sidebar from every page, with live progress.
* Actions confirm with toasts rather than blocking dialogs.
* The lead table becomes cards on phones; every page is responsive.
* Column headers sort; filters are URL-addressable, so a filtered view is a
  link you can share or bookmark.

### Search performance

Lead search runs through an **FTS5 full-text index** with `bm25()` relevance
ranking, not `LIKE '%term%'` - which cannot use an index and scans every row.
The index is external-content (the `leads` table stays the single source of
truth) and kept live by insert/update/delete triggers.

One trap worth recording, because it cost time: `SELECT COUNT(*)` on an
external-content FTS table reads the *content* table, not the index, so it
always matches the row count and can never tell you the index is empty. The
build state is tracked with a marker in `meta` instead.

## Requirements

- Python 3.11+
- Node.js 20+
- Chromium, installed once with `python -m playwright install chromium`
- No API key is required. A Gemini key ([aistudio.google.com/apikey](https://aistudio.google.com/apikey)) is optional and only improves outreach copy

## Setup

```bash
python -m venv .venv && .venv/Scripts/activate   # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env        # set LEADGEN_SECRET_KEY; everything else has a default
cd web && npm install && cd ..
```

Run the API and the frontend in two terminals:

```bash
python run.py
```

```bash
cd web && npm run dev
```

- App: http://localhost:3600
- API: http://localhost:8000
- API docs: http://localhost:8000/docs

The first account you register needs a licence key to unlock searching. A demo
key, `LEAD-PRO-2026-DEMO`, is seeded on a fresh database.

## Docker

```bash
docker compose up -d --build
```

Set `LEADGEN_SECRET_KEY` (required) in a `.env` file first.
The API container ships its own Chromium and is given 1 GB of shared memory,
which headless Chrome needs.

## Configuration

Everything is an environment variable; see [.env.example](.env.example) for the
full list. The ones worth knowing:

| Variable | Default | Notes |
| --- | --- | --- |
| `LEADGEN_SECRET_KEY` | — | **Required in production.** Signs session cookies. |
| `LEADGEN_EMAIL_CONCURRENCY` | `25` | Websites fetched at once. |
| `LEADGEN_EMAIL_MAX_PAGES` | `4` | Homepage plus follow-up pages per site. |
| `LEADGEN_EMAIL_USE_CACHE` | `1` | Fetch each domain once, ever. |
| `LEADGEN_EMAIL_SEARCH_FALLBACK` | `1` | Look the business up by name when its site gives nothing. |
| `LEADGEN_EMAIL_SEARCH_MAX_LOOKUPS` | `150` | Search budget per run. |
| `LEADGEN_EMAIL_SEARCH_CONCURRENCY` | `3` | Keep low; search engines rate-limit. |
| `LEADGEN_EMAIL_SLOW_RETRY_TIMEOUT` | `20` | Longer second attempt for slow hosting. |
| `LEADGEN_AI_EMAIL_BATCH_SIZE` | `20` | Businesses per AI request (opt-in feature). |
| `LEADGEN_AI_EMAIL_CONCURRENCY` | `3` | AI requests in flight. |
| `LEADGEN_SMTP_VERIFY` | `0` | Turn on SMTP verification and pattern discovery. |
| `LEADGEN_SMTP_HELO_HOST` | `localhost` | Use a domain you control. |
| `LEADGEN_SMTP_MAIL_FROM` | `verify@localhost` | Envelope sender for probes. |
| `LEADGEN_SMTP_CONCURRENCY` | `4` | Domains probed at once. Keep low. |
| `LEADGEN_SMTP_MAX_CANDIDATES` | `12` | Mailboxes tried per domain. |
| `LEADGEN_SMTP_PATTERNS` | built-in | Comma-separated role mailboxes to try. |
| `GEMINI_API_KEY` | — | **Optional.** Outreach copy only. Without it, built-in templates are used. |
| `GEMINI_MODEL` | `gemini-3.6-flash` | `gemini-2.5-flash` is closed to new API keys. |
| `LEADGEN_GMAPS_DETAIL_WORKERS` | `5` | Parallel browsers. The main speed lever — each one is a Chromium process, so raise it only with memory to spare. |
| `LEADGEN_GMAPS_TILING` | `1` | Search wide areas tile by tile to get past Maps' ~120-result cap. |
| `LEADGEN_GMAPS_MAX_CELLS` | `25` | Most tiles per search. More coverage, more time. |
| `LEADGEN_GMAPS_SINGLE_SEARCH_YIELD` | `90` | Above this target, tiling switches on. |
| `LEADGEN_GMAPS_SATURATION` | `45` | New results in a cell above which it is split into four. |
| `LEADGEN_GMAPS_MAX_DEPTH` | `3` | How many times a cell may be subdivided. |
| `LEADGEN_GMAPS_MAX_SEARCHES` | `400` | Ceiling on searches per run, so unlimited still ends. |
| `SERPER_API_KEY` | — | Optional. Swaps DuckDuckGo for real Google results. |

## Email lookup modes

- **Find email addresses** (default) — after collecting the businesses, read each website for its published addresses and social links.
- **Skip email lookup** — collect names, phones, addresses, websites and ratings only. You can look up emails later by re-running.

## Layout

```
server/            FastAPI backend
  main.py          app, CORS, security headers
  settings.py      every tunable, read from the environment
  db.py            SQLite schema and access
  security.py      bcrypt + signed-cookie sessions
  jobs.py          job runner, persistence, SSE fan-out
  sources/
    gmaps.py       Google Maps scraper (parallel detail pool)
    websearch.py   keyword -> business websites
    browser.py     Playwright session: one browser, many pages
  enrich/
    emails.py      website email discovery (free, no model)
    smtp_verify.py mailbox verification + role-mailbox discovery
    ai_emails.py   opt-in AI suggestions, batched + MX-checked, flagged unverified
    gemini.py      outreach copy only (optional)
  routes/          auth, jobs, leads, dashboard, outreach, workspace
web/               Next.js 16 frontend (App Router, TypeScript, Tailwind 4)
  app/             dashboard, analytics, search, jobs, leads, lists, outreach, settings
  components/      AppShell, CommandPalette, LeadDrawer, shared UI
  lib/             api client, auth, toasts, types
scripts/migrate.py one-shot migration from the old schema
```

## Migrating an existing database

```bash
python -m scripts.migrate --db leadgen.db
```

It backs the file up first, folds `scrape_history` + `gmaps_sessions` into
`jobs`, merges `leads` + `gmaps_session_leads` into `leads`, strips placeholder
values like `N/A`, recovers **all** emails and phones from the old
semicolon-joined fields into the new arrays (dropping telemetry addresses, date
ranges and numeric ids that the old regex had swept up), and drops the 22 tables
that were never written to. Legacy tables are kept until you rerun with
`--drop-legacy`.

## Notes

Scraping Google Maps depends on markup Google changes without notice. The feed
parser reads `aria-label`s where it can, which are more stable than class names,
but expect occasional maintenance. Use the tool in line with the platforms'
terms and applicable privacy law.
