# jobhunt

A personal job-search agent. It reads public ATS APIs, throws away the ~99% that
don't fit you, scores what's left against your resume, and emails you a ranked
digest of every match with an Apply link. Run it on a schedule and it becomes a
"tell me the moment a company I'd join posts a role I'd want" alert.

**It never submits an application.** It finds, filters and ranks. You read the
digest, open the JD, and press submit yourself.

```
2000 postings  →  40 candidates  →  50+ ranked in your inbox
   fetch          regex/location      LLM screen (cheap)
                  /freshness gate     one line per job, Apply link
                  (free, no LLM)      drafting is opt-in, off by default
```

The digest is **list-only**: one compact row per job — score, title, company,
location, a one-line reason, Apply button. Screening is the only token cost, so
a run can surface 50+ roles without a token bill or a wall of text. A full
per-job application kit is still available on demand (`run --draft-top N`).

> **New to Python?** Read **[SETUP.md](SETUP.md)** instead — it's a step-by-step
> guide that assumes you have nothing installed. This README assumes you're
> comfortable with a terminal.

---

## Run it in 30 seconds, no API key

```bash
git clone <your-repo> && cd jobhunt
python -m venv .venv && .venv/Scripts/activate      # Windows
# python -m venv .venv && source .venv/bin/activate # macOS/Linux
pip install -r requirements.txt

python -m jobhunt run --mock --scorer keyword
```

`--mock` runs bundled fixtures through the **real parsers** — no network.
`--scorer keyword` swaps the LLM for a dumb token-overlap stub, so the whole
pipeline runs with no secrets configured. You should see:

```
[2/5] filtering
  prefilter: 14 -> 5 (dropped title=6 experience=1 location=2 stale=0)
  new since last run: 5
[3/5] screening 5 jobs (keyword stub — DEV ONLY)
  0 scored >= 6.0; showing 0
[4/5] drafting skipped (list-only; pass --draft-top N to enable)
[5/5] digest
  wrote out/digest.html

funnel: 14 scanned -> 5 passed filters -> 5 new -> 0 in digest
```

Open `out/digest.html` in a browser. That's the email you'd have received.

> The keyword scorer is **dev-only** — it just counts shared tokens, so it
> routinely scores everything below the bar (hence `0 in digest` above). It
> exists to prove the plumbing runs end-to-end without a key, never to build a
> digest you'd act on. Point a real provider at it (next section) to get scores.

---

## Set it up for real

### 1. Point it at companies you'd actually join

Edit `companies.yaml`. The slug is the last path segment of a company's public
careers board:

| Board URL | `ats` | `slug` |
|---|---|---|
| `boards.greenhouse.io/stripe` | `greenhouse` | `stripe` |
| `jobs.lever.co/netlify` | `lever` | `netlify` |
| `jobs.ashbyhq.com/ramp` | `ashby` | `ramp` |
| `apply.workable.com/hotstar` | `workable` | `hotstar` |
| `jobs.smartrecruiters.com/Bosch` | `smartrecruiters` | `Bosch` |
| `example.recruitee.com` | `recruitee` | `example` |
| `remotive.com/remote-jobs` | `remotive` | `software-development\|devops` |
| `arbeitnow.com/api/job-board-api` | `arbeitnow` | `remote\|software` |

**SmartRecruiters** is where many global enterprises run their India roles
(Bosch, Visa, McDonald's, …). Its list endpoint omits the job description, so
jobhunt fetches the JD **only** for the handful that clear the free prefilter —
and bounds the list to India server-side (`country=in`) so one 5000-role
employer can't dominate a run. The slug is the company name as it appears in the
board URL, capitalisation included.

The shipped list is **examples** — verify each before trusting the output.
Companies migrate between ATS vendors and slugs go dead. A dead slug prints an
HTTP status and returns nothing; it never kills the run. Watch the per-board
counts on stdout: a board reporting 0 every day is a slug that needs fixing.

Scaling toward hundreds of boards? Every added slug is a chance to be wrong, so
check them in bulk instead of trusting the list:

```bash
python -m jobhunt validate                      # poll every board, print live/dead
python -m jobhunt validate --prune live.yaml    # write only the live boards out
```

Boards are polled concurrently, so a few hundred slugs validate in seconds.
Prune the dead ones rather than paying to poll nothing four times a day.

**No LinkedIn or Naukri.** Neither has a public API and scraping them violates
their terms of service. The ATS and Remotive endpoints above are documented,
unauthenticated, and intended to be read. Remotive links remain direct Remotive
URLs so the required source attribution is preserved.

**YC coverage:** YC does not expose a documented public Work at a Startup jobs
API, and its jobs endpoint rejects unauthenticated automated requests. The
shipped board list therefore includes YC companies that publish through a
supported public ATS (for example Plane) without scraping Work at a Startup.

### 2. Tune the filters

`config.yaml` holds the deterministic gate that runs **before** any LLM call.
This is the whole cost story — get it right and you spend cents a day.

```yaml
filters:
  include_titles: ['\bsde\b', 'software development engineer', ...]
  exclude_titles: ['\b(staff|principal)\b', '\b(manager)\b', ...]
  locations: [bangalore, bengaluru, hyderabad, pune, ..., india]
  allow_remote: true
  max_age_days: 30
  max_experience_years: 2
max_screen: 400           # token guard: most jobs to screen in one run
digest_min_score: 3.0     # mail 3+ scores; skip email only when the list is empty
digest_size: 60           # cap the list length
```

> **`sde` does not match "Software Development Engineer".** They share no
> substring. Use `\bsde\b` for the acronym *and* list the spelled-out variants
> separately, or you'll silently miss half of Amazon-style postings. There's a
> test pinning this.

### 3. Build your profile

```bash
cp .env.example .env      # add GEMINI_API_KEY and DEEPSEEK_API_KEY
python -m jobhunt profile --resume resume.pdf
```

PDFs go over as a base64 document block (Anthropic and Gemini read them
natively — no OCR, no text extraction library). `.txt` and `.md` also work and
are the fallback for providers that can't take documents, including DeepSeek —
so to build the profile with a DeepSeek-only setup, pass a `.txt`/`.md` resume,
or set `DRAFT_PROVIDER=gemini` just for the one-off profile step.

This writes `profile.json`. It's gitignored — read it, fix anything the model
got wrong, and keep it out of version control.

### 4. Run it

```bash
python -m jobhunt run                    # build the digest (list only)
python -m jobhunt run --send             # ...and email it
python -m jobhunt run --limit 10         # cost guard while tuning
python -m jobhunt run --draft-top 3      # also write full kits for the top 3
```

Drafting is **off by default** — the digest is the ranked list, and screening
is the only token cost. `--draft-top N` opts the top N into a full application
kit (uses the draft-stage model; costs more), for the roles you're serious about.

---

## `.env` — Gemini 3 and DeepSeek

Copy [`.env.example`](.env.example) to `.env`. The recommended setup is **Gemini 3**
for every call, with **DeepSeek** as automatic fallback if Gemini 429s or errors.

```bash
LLM_PROVIDER=gemini
SCREEN_PROVIDER=gemini
DRAFT_PROVIDER=gemini
GEMINI_API_KEY=your-gemini-key
SCREEN_MODEL=gemini-3.5-flash-lite
DRAFT_MODEL=gemini-3.6-flash

FALLBACK_PROVIDER=deepseek
FALLBACK_MODEL=deepseek-chat
DEEPSEEK_API_KEY=your-deepseek-key
# DEEP_SEEK_API_KEY=          # alias; also works

SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASS=your-16-char-app-password
MAIL_TO=you@gmail.com
```

DeepSeek-only (no Gemini). `profile` then needs a `.txt` / `.md` resume:

```bash
LLM_PROVIDER=deepseek
SCREEN_PROVIDER=deepseek
DRAFT_PROVIDER=deepseek
SCREEN_MODEL=deepseek-chat
DRAFT_MODEL=deepseek-chat
DEEPSEEK_API_KEY=your-deepseek-key
```

| Variable | What |
|---|---|
| `LLM_PROVIDER` | default for both stages: `gemini` or `deepseek` |
| `SCREEN_PROVIDER` / `DRAFT_PROVIDER` | override one stage |
| `SCREEN_MODEL` | Gemini 3 cheap pass: `gemini-3.5-flash-lite` |
| `DRAFT_MODEL` | Gemini 3 quality pass: `gemini-3.6-flash` |
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/apikey) |
| `DEEPSEEK_API_KEY` | [DeepSeek platform](https://platform.deepseek.com) |
| `FALLBACK_PROVIDER` / `FALLBACK_MODEL` | used when Gemini raises; default `deepseek` / `deepseek-chat` |
| `SMTP_*` / `MAIL_TO` | digest email (Gmail App Password, no spaces) |

| Provider | Value | Key | PDF resumes |
|---|---|---|---|
| Google Gemini 3 | `gemini` | `GEMINI_API_KEY` | yes |
| DeepSeek | `deepseek` | `DEEPSEEK_API_KEY` | no |
| Groq | `groq` | `GROQ_API_KEY` | no |
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` | yes |
| Ollama | `ollama` | none | no |

Never commit `.env`. If a key leaked, regenerate it.

---

## Tracking

The seen-store is both the dedupe index and the application tracker — a job
you've already been shown is never shown again. Locally that's `seen.json` on
disk (gitignored: it's yours, and shipping one would mark every job as
already-seen for anyone who cloned the repo). On a hosted deploy it's Upstash
Redis instead, chosen automatically when the Upstash env vars are set — see
[Deploy on Render](#deploy-on-render-runs-4day). Both back the same commands:

```bash
python -m jobhunt applied "greenhouse:stripe:5501001"   # id is in the digest
python -m jobhunt stats                                 # + CSV export
```

`out/tracker.csv` opens in any spreadsheet.

---

## Scheduling

[`.github/workflows/daily.yml`](.github/workflows/daily.yml) runs **four times a
day** (07:05 / 12:35 / 17:05 / 21:35 IST). `seen.json` is carried between runs
with `actions/cache`, not committed — it's personal, and a `seen.json` in the
repo would mark every job as already-seen for anyone who cloned it. Nothing
personal ever enters git. Keep the repo **private** so `PROFILE_JSON` and SMTP
secrets stay off a public fork.

Repository **secrets** to set (Settings → Secrets and variables → Actions):

| Secret | What |
|---|---|
| `PROFILE_JSON` | the entire contents of your local `profile.json` |
| `GEMINI_API_KEY` | (or `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY` / `GROQ_API_KEY`) |
| `SMTP_USER` / `SMTP_PASS` | Gmail address + **App Password**, not your login |
| `MAIL_TO` | where the digest goes |

Optional repository **variables**: `LLM_PROVIDER`, `SCREEN_PROVIDER`,
`DRAFT_PROVIDER`, `SCREEN_MODEL`, `DRAFT_MODEL`.

Trigger it by hand first — Actions → *daily job digest* → *Run workflow*, with
`dry_run` ticked to build the digest artifact without emailing.

Gmail needs an [App Password](https://myaccount.google.com/apppasswords); your
normal password stops working once 2FA is on.

---

## Deploy on Render (runs 4×/day)

The repo ships a [`render.yaml`](render.yaml) Blueprint and a tiny web wrapper
([`jobhunt/server.py`](jobhunt/server.py)) so a free Render service can run the
pipeline on a schedule — no always-on cost, no committed secrets.

**Why a web service and not a cron job?** Render's native Cron Jobs are a paid
add-on. A free **web service** plus a free external pinger does the same thing:
the pinger POSTs to `/run` four times a day, and the service runs the pipeline.

**Why Redis?** Render's free disk is wiped on every spin-down, so `seen.json`
wouldn't survive between runs and every job would look new every time. The
seen-store therefore lives in **Upstash Redis** (free tier, HTTP REST) and is
selected automatically once its two env vars are set.

### One-time setup

1. **Upstash** — create a free Redis database at [upstash.com](https://upstash.com).
   From the database's *REST API* section copy `UPSTASH_REDIS_REST_URL` and
   `UPSTASH_REDIS_REST_TOKEN`.
2. **A run token** — invent a shared secret so only your pinger can trigger runs:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
3. **Render** — push this repo, then in Render: **New + → Blueprint**, point it at
   the repo. It reads `render.yaml` and creates the service. Fill the `sync:false`
   secrets in the dashboard:

   | Env var | Value |
   |---|---|
   | `DEEPSEEK_API_KEY` | your DeepSeek key |
   | `UPSTASH_REDIS_REST_URL` / `..._TOKEN` | from step 1 |
   | `RUN_TOKEN` | from step 2 |
   | `PROFILE_JSON` | the **one-line** contents of your local `profile.json` |
   | `SMTP_USER` / `SMTP_PASS` / `MAIL_TO` | Gmail + App Password + recipient |

   `profile.json` is gitignored, so on boot the service writes it to disk from
   `PROFILE_JSON` (same trick the GitHub workflow uses).
4. **The pinger** — at [cron-job.org](https://cron-job.org) (free) create jobs that
   **POST** to `https://<your-service>.onrender.com/run` with a header
   `X-Run-Token: <your RUN_TOKEN>`. Four times spread across the day works well,
   e.g. 07:05 / 12:35 / 17:05 / 21:35 IST. `/run` returns `202` immediately and
   does the fetch+screen+email in the background, so the ping never times out.

### The endpoints

| Route | Purpose |
|---|---|
| `GET /health` | liveness (Render's health check hits this) |
| `GET /` | status JSON: last run result + tracker counts |
| `POST /run` | trigger a run — requires the `RUN_TOKEN`; returns `202` |

A Redis lock (`SET NX EX`) means two near-simultaneous pings can't both run.
The first ping of the day also wakes the spun-down service; that ping does the
waking and the run proceeds normally.

Test it once deployed:

```bash
curl -X POST https://<your-service>.onrender.com/run -H "X-Run-Token: <token>"
curl https://<your-service>.onrender.com/          # see the last-run status
```

> The **same code** runs locally, in GitHub Actions, and on Render — only the
> seen-store backend and the trigger differ. Pick whichever scheduler you like;
> you don't need both Actions and Render.

---

## Layout

```
jobhunt/
  fetch.py       Job dataclass, strip_html, pure source parsers, concurrent fetch_all
  prefilter.py   title/location/freshness/experience gate — no LLM, no cost
  providers.py   the swappable provider interface + 6 backends (incl. DeepSeek)
  llm.py         screen() / draft() / build_profile() / keyword stub
  digest.py      compact HTML list email (inline CSS only — Gmail strips <style>)
  mailer.py      SMTP
  store.py       seen-store: FileStore (disk) or RedisStore (Upstash) + tracker/CSV
  server.py      Flask /health /run wrapper for Render's 4x/day pinger
  mock.py        fixtures in each ATS's native JSON shape
  cli.py         argparse: profile / run / validate / applied / stats
config.yaml      filters, screen/digest knobs, paths
companies.yaml   boards to poll
render.yaml      Render Blueprint (free web service + env var declarations)
tests/           88 tests, no network, no key
```

HTTP is kept out of the parsers on purpose. Each `parse_*(slug, company, body)`
takes already-decoded JSON and returns `list[Job]`, which is what makes `--mock`
exercise the real code path instead of a parallel implementation.

Every job gets `job_id = "{ats}:{slug}:{id}"` — globally unique, so the same
role posted on two boards is still two rows, and a re-run never duplicates.

### ATS quirks the parsers handle

- **Greenhouse** — `content` is HTML-entity-escaped HTML. Unescape *before*
  stripping tags and again after, or you ship `&amp;` into the prompt.
- **Lever** — `createdAt` is epoch **milliseconds**. The full JD is split across
  `descriptionPlain` **+** `lists[].text` **+** `lists[].content` **+**
  `additionalPlain`; concatenate all four or you lose the requirements section
  and every job looks unqualified.
- **Ashby** — skip `isListed: false`; those are unpublished drafts.
- **SmartRecruiters** — the list endpoint has **no JD**. The parser sets a
  `detail_url` and leaves the description empty; `hydrate_descriptions()` fetches
  the body only for jobs that survived the prefilter, so a 5000-role employer
  costs a few extra fetches, not 5000. The list is bounded to India server-side.

---

## Tests

```bash
python -m pytest tests -q
```

No network, no API key, no cost. The suite covers:

- each parser against fixtures in its **native** ATS shape
- the two bugs that cost me an evening each: Lever's epoch-ms timestamps
  (fixture dates are generated relative to *now*, never hardcoded, so they
  can't silently age past the freshness gate) and the `\bsde\b` regex
- prefilter rejects the planted junk: wrong seniority, wrong city, wrong
  function, a stale posting, an unlisted Ashby draft
- the LLM layer with the provider stubbed: batching splits at the configured
  size, JD truncation is applied before send, fenced/preamble/object-or-array
  JSON all parse, scores land on the right job when returned out of order, a
  failed batch warns and the run continues, and the draft kit always has every
  key the digest renders

---

## Cost

List-only mode makes **screening the only token cost** — no per-job drafting
unless you ask with `--draft-top N`. With a tight `config.yaml` and DeepSeek
screening, a day of four runs lands around single-digit rupees: the prefilter
means nothing reaches a model until it has passed title, location, freshness and
experience, and DeepSeek's prompt-cache bills the repeated profile prefix once.
Set `SCREEN_PROVIDER=gemini` or `groq` and screening is free.

`max_screen` in `config.yaml` is a hard ceiling on jobs screened per run, and
`--limit` is a smaller guard while tuning filters — either keeps a bad regex or
a first-run flood from running up a bill.
