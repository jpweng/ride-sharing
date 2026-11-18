# Working on Ride and Rail

Rules for anyone changing this repository, people and Claude alike. The app itself is described in
[README.md](README.md).

## Essentials

- **Merging to `main` deploys to production** (`.github/workflows/deploy.yml`, live at
  rideandrail.ch). Every change goes through a pull request, and `main` is protected (the Tests
  check must pass and the branch must be up to date). Push to `main` directly only when the owner
  explicitly asks for it for that change.
- **The repository is public.** Commits, pull requests and comments stay technical: no business
  strategy, prices or fees beyond what the code does, partner names or plans, investor details or
  personal data.
- **English** for all code, comments, docs, commit messages and pull requests, whatever language
  the conversation is in.
- Run `uv run pytest` before pushing. New behaviour comes with tests.
- Every UI text goes into all four languages (`frontend/i18n/{en,de,fr,it}/<prefix>.json`), with
  formal address ("Sie", "vous", "Lei"). `backend/tests/test_i18n.py` enforces it.
- Never run `terraform apply` or AWS commands that change anything without the owner's explicit
  go-ahead for that command.

## Branches: avoiding repeated merge conflicts

Several branches editing the same files at the same time conflict every time one of them is
merged. These rules keep that rare.

### Where new code goes

A feature adds its own files instead of editing shared ones:

- **API routes:** a new module in `backend/routes/` with `router = APIRouter()`; `main.py` includes
  every module there automatically. Shared helpers (errors, sessions, `Member`/`Writer`) are in
  `backend/api.py`.
- **Translations:** a new `frontend/i18n/<lang>/<prefix>.json` in each of the four languages, for
  the feature's `<prefix>.*` keys (full flat keys, sorted). Keys with an existing prefix go in that
  prefix's file, in alphabetical order. The browser gets the merged `i18n/<lang>.json`.
- **Database:** a new file `backend/migrations/YYYYMMDDHHMM_short_name.py` with an idempotent
  `def apply(conn)`; every migration runs at every start. Data-access functions stay in
  `backend/database.py`.
- **Styles:** a new `frontend/styles/<feature>.css`, linked right after `style.css` by the pages
  that use it. `style.css` is for the base, layout and shared components.
- **Docs:** a new `docs/<topic>.md`, linked from the README's Documentation list.
- New frontend files are in the service worker's offline copy automatically (the server lists them
  when serving `/sw.js`).

Still shared, so conflicts happen there: `frontend/index.html`, `frontend/app.js`,
`backend/tests/conftest.py`, `deploy/compose.yaml`, `.github/workflows/deploy.yml`, and the
README's Documentation list.

### Branch rules

- **Before starting a branch**, `git fetch`, branch from `origin/main`, and look at the open pull
  requests (`gh pr list`, `gh pr diff <n> --name-only`). If one of them changes the same shared
  files in the same area, wait for it to be merged or build on top of its branch, and say so in
  the pull request ("Builds on #12, merge after it").
- **Foundations first.** A change other work depends on (identity and sign-in, the database
  schema, prices, bookings, payments, the page layout) is its own pull request, merged before the
  work that uses it starts. Dependent work branches from `main` after that merge, never in
  parallel with it.
- **Small and short-lived.** One topic per branch. Open it as a draft early, and get it merged
  within a day or two. At most three branches open at once. When several start together, agree on
  the order they will be merged in and write it in each pull request.
- **Parallel agents get separate files.** When Claude runs several agents at once, each gets
  work that touches different files; never two agents on the same shared file.
- **Keeping a branch up to date: merge, never rebase.** `git fetch && git merge origin/main`,
  resolve, run the tests, push. Never force-push a branch that has a pull request. With
  `git config rerere.enabled true`, git remembers how a conflict was resolved and reuses it.
- **After any pull request is merged, update the other open ones straight away**, while the
  conflicts are small. Resolving conflicts is Claude's job, not the owner's: in a session, or by
  commenting `@claude update this branch from main and fix the conflicts` on the pull request.
- **Don't touch what you don't need to.** No reformatting, reordering or renaming in passing; it
  turns a small change into conflicts for everyone else.
- Merge method: merge commits only (squash and rebase merges are disabled), and merged branches
  are deleted automatically.

## Every pull request is safe to deploy the moment it's merged

- If a change needs anything outside the code first (an AWS resource or `terraform apply`, a
  GitHub variable or secret, DNS, an external account, data to load), either:
  - it ships **switched off**: an environment variable or setting, off when missing, turned on
    once the setup is done; or
  - the setup goes in **its own pull request**, merged and applied first.
- **A missing setting never breaks the site.** A new variable that isn't set yet must leave
  existing features working (log a warning, hide the new feature), never fail at startup or on
  every request.
- New environment variables: pass them through `deploy/compose.yaml` and the env file in
  `.github/workflows/deploy.yml`, with a safe default.
- Database changes are additive and backward compatible (new tables or nullable columns), each in
  its own idempotent migration file in `backend/migrations/`, applied at startup.
- The pull request template's **Before merging** section lists every setup step, with who does
  it. If it isn't empty, the pull request is not ready to merge until the steps are done.
