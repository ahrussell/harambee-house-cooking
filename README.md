# harambee-house-cooking

A small Flask web app used by Harambee House to plan who cooks dinner on which
day. Each week housemates sign up with their availability (eat / cook / away)
and how many guests they are bringing. An integer program then assigns one chef
per day, spreading cooking shifts as evenly as possible while using a "mooch
score" (eating vs. cooking history) as a tiebreaker.

## Stack

- **Flask 3** (`app.py`) with Jinja templates, no JS framework/build step.
- **PostgreSQL** via `psycopg2` (`DATABASE_URL` env var).
- **PuLP** (CBC solver) for the chef-assignment optimization.
- **Gunicorn** in production, deployed to **Fly.io** (`fly.toml`, `Dockerfile`).

## Repository layout

| Path | Purpose |
| --- | --- |
| `app.py` | Entire backend: routes, DB schema/init, scheduling + mooch logic. |
| `templates/index.html` | Signup page (also the `/` landing page). |
| `templates/schedule.html` | Schedule view. |
| `update_name.py` | One-off manual data migration for renaming a housemate everywhere. Not part of the app. |
| `requirements.txt` | Pinned Python dependencies. |
| `Dockerfile`, `fly.toml`, `.dockerignore` | Fly.io deployment. |

There is **no test suite** and no linter configuration.

## Running locally

Requires Python 3.11+ and a reachable PostgreSQL database.

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export DATABASE_URL="postgresql://user:pass@localhost:5432/harambee"
python app.py
```

Then open <http://localhost:8080>. The app calls `init_db()` at import time and
creates the `signups` and `schedules` tables if missing, so no separate migration
step is needed.

Environment variables:

| Variable | Default | Notes |
| --- | --- | --- |
| `DATABASE_URL` | none (required) | Any libpq URL. |
| `PORT` | `8080` | Only read when running `app.py` directly. |
| `FLASK_ENV` | unset | Anything other than `production` enables Flask debug mode. |

## Deployment

Deployed to Fly.io from the `Dockerfile` (Gunicorn, port 8080, primary region
`ams`). Typical flow:

```bash
fly deploy
fly secrets set DATABASE_URL=...
```

`fly.toml` sets `FLASK_ENV=production` and auto-stops machines when idle.

## Data model

Two tables, both keyed by a Monday `week_start` date string (`YYYY-MM-DD`):

```sql
signups (
    week_start  TEXT,
    person_name TEXT,
    data        JSONB,
    PRIMARY KEY (week_start, person_name)
)

schedules (
    week_start  TEXT PRIMARY KEY,
    data        JSONB
)
```

A signup document:

```json
{
  "week_start": "2026-11-09",
  "person": {"name": "Asim"},
  "days": [
    {"day": "Monday", "availability": "Wants to Eat", "guests": 0}
  ]
}
```

`availability` is one of `Wants to Eat`, `Available to Cook`, `Unavailable`.
A schedule document lists one entry per day with `chef` and `people`
(`[{"name": "...", "guests": 0}]`), plus a `schedule_objective_fn` block when
the solver found an optimal solution. Schedules are recomputed and persisted on
every successful signup submission for that week.

## Weeks are stored in UTC

`get_week_start(weeks_ago)` returns Monday 00:00 **UTC**. The `weeks_ago`
convention is unusual and used throughout the code and URLs:

| `weeks_ago` | Meaning |
| --- | --- |
| `0` | Next week |
| `1` | This week (default) |
| `2` | Last week |
| `n` | `n - 1` weeks ago |

## HTTP API

All routes return HTML for browsers and JSON when the request sends
`Accept: application/json`.

| Method | Route | Description |
| --- | --- | --- |
| `GET` | `/`, `/signup`, `/signup/<name>`, `/signup/<name>/<weeks_ago>` | Signup page. JSON variant returns that person's signup (empty skeleton if none stored). |
| `POST` | `/submit_signup` | Body: `{person: {name}, weeks_ago, days: [...]}`. Upserts the signup and regenerates the week's schedule. Returns `400` with `{error}` on invalid/missing name. |
| `GET` | `/schedule`, `/schedule/<weeks_ago>` | Schedule page. JSON variant returns the stored schedule or an empty one. |
| `GET` | `/stats/<name>` | JSON only (`404` otherwise). Last 4 weeks of eating/cooking counts and the mooch score. |

## Domain logic (`app.py`)

### Availability and guests

For each day a person picks an emoji state cycling EAT -> COOK -> UNAVAILABLE.
Availability is stored as the string values above. Guests are added on top of
the person for `people` lists and cooking-effort scoring.

### Mooch score

`calculate_eating_cooking_scores` weights the last 4 weeks `8, 4, 2, 1`
(most recent first):

- Eating score: +1 per dinner eaten.
- Cooking score: per cooked dinner, `4` if cooking for >=7 people, `3` for >=4,
  `2` for >=2, else `0` (people includes guests).
- `max_possible_eating_score` is `7 * weight` summed, i.e. every dinner.

`calculate_mooch_score` returns `eating / cooking`; `1` when the person has no
history, and `eating * max_possible_eating_score` when they have eaten but never
cooked. Higher = eats more than they cook.

### Chef assignment (`create_schedule`)

PuLP binary variables `x[day, chef]` plus `shift[chef, k]` (cooks at least `k`
times this week). Objective: minimize `sum(k * shift[chef, k])` so extra shifts
for the same person get progressively more expensive (even spreading), minus a
small `0.01 * normalized_mooch` term that only breaks ties in favor of mooch
scores. Constraints:

1. Every day with at least one available chef gets exactly one chef.
2. A chef can only be assigned on days they marked `Available to Cook`.
3. Assignments equal the number of shifts used.

Days with no available cook keep `chef: null`. The schedule is only written
when the solver status is `Optimal`.

## Gotchas for contributors

- **Housemates are hardcoded** in the `HOUSEMATES` list in `app.py`. The
  dropdown in `templates/index.html` is generated by iterating that list, so
  adding/removing a housemate only means editing `HOUSEMATES`. Recently active
  signups (last 4 weeks) are automatically appended from the DB by
  `get_people()`.
- **Signup is an upsert.** Re-submitting overwrites that person's row for the
  week; it does not append.
- **Schedule regeneration is global per week.** Any single submission recomputes
  the whole week from all stored signups.
- **No auth.** Anyone can submit under any name; the name list is just a hint.
- **Renaming a housemate** requires editing `update_name.py` (`OLD_NAME` /
  `NEW_NAME`), running it with `DATABASE_URL` set, and updating
  `HOUSEMATES` in `app.py`.
- **`Accept: application/json` must match exactly**; the JSON branches compare
  the header with `==`, so `application/json; charset=utf-8` falls through to
  HTML.
