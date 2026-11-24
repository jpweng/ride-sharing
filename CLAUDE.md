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
- **One description of the service, everywhere.** What Ride and Rail offers is said in the About
  page (`frontend/about.html`, four languages), the README, `docs/`, the privacy notice
  (`frontend/privacy.html`), the app's manifest and the store listings (`mobile/`). They must agree
  with each other and with what the site and the apps do, at all times: a change to what users can
  do, see or pay updates every one of them that mentions it, in the same pull request.
  [docs/about-page.md](docs/about-page.md) lists where each statement of the About page comes from.

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
  `def apply(conn)`; every migration runs at every start. A new table's queries can go in the
  feature's own module (`with database.get_conn() as conn:`); the existing ones stay in
  `backend/database.py`.
- **Styles:** a new `frontend/styles/<feature>.css`, linked right after `style.css` by the pages
  that use it. `style.css` is for the base, layout and shared components.
- **Browser code:** a new `frontend/<feature>.js` for a feature's own section or page, loaded after
  `app.js` by `index.html`. It uses `app.js`'s globals (the map, `points`, `PAGES`, `fetchJson`,
  `t`) and hooks in by wrapping a page's `onShow`, as `areas.js` does. `app.js` keeps what the
  pages share.
- **Docs:** a new `docs/<topic>.md`, linked from the README's Documentation list. The feature's
  endpoints are described there (access level, error codes), not in `docs/api.md`.
- New frontend files are in the service worker's offline copy automatically (the server lists them
  when serving `/sw.js`).

Still shared, so conflicts happen there: `frontend/index.html`, `frontend/app.js`,
`backend/tests/conftest.py`, `deploy/compose.yaml`, `.github/workflows/deploy.yml`, the README's
Documentation list, and the cross-cutting translation files `frontend/i18n/<lang>/errors.json` (an
API error code's text) and `nav.json` (a new tab). A new table also touches
`backend/database.py` (`delete_account()` lists every user table) and `backend/maintenance.py`
(data retention).

### Branch rules

- **Before starting a branch**, `git fetch`, branch from `origin/main`, and look at what is in
  progress: the open pull requests (`gh pr list`, `gh pr diff <n> --name-only`) and the other
  sessions' worktrees (`git worktree list`). If one of them changes the same shared
  files in the same area, wait for it to be merged or build on top of its branch, and say so in
  the pull request ("Builds on #12, merge after it").
- **Foundations first.** A change other work depends on (identity and sign-in, the database
  schema, prices, bookings, payments, the page layout) is its own pull request, merged before the
  work that uses it starts. Dependent work branches from `main` after that merge, never in
  parallel with it.
- **Small and short-lived.** One topic per branch. Open it as a draft with its first commit,
  before the bulk of the work: a branch without a pull request is invisible to the other sessions.
  Get it merged within a day or two. At most three branches open at once. When several start
  together, agree on the order they will be merged in and write it in each pull request.
- **Parallel agents get separate files.** When Claude runs several agents at once, each gets
  work that touches different files; never two agents on the same shared file.
- **Keeping a branch up to date: merge, never rebase.** `git fetch && git merge origin/main`,
  resolve, run the tests, push. Never force-push a branch that has a pull request. With
  `git config rerere.enabled true`, git remembers how a conflict was resolved and reuses it.
- **After any pull request is merged, the other open ones are updated straight away**, while the
  conflicts are small: each session updates its own branches, not another session's (see below).
  Resolving conflicts is Claude's job, not the owner's: in the branch's session, or by
  commenting `@claude update this branch from main and fix the conflicts` on the pull request.
- **Don't touch what you don't need to.** No reformatting, reordering or renaming in passing; it
  turns a small change into conflicts for everyone else.
- Merge method: merge commits only (squash and rebase merges are disabled), and merged branches
  are deleted automatically.

## Several sessions at once

Several Claude sessions often work on this repository at the same time. One folder has one branch
and one set of uncommitted files: two sessions in the same folder commit, overwrite or switch away
each other's work without noticing.

- **One worktree per session.** A session that changes files first makes its own worktree from
  `origin/main` and works only there:
  `git worktree add .claude/worktrees/<topic> -b <branch> origin/main`. The main folder is the
  owner's: a session doesn't switch its branch, pull into it or edit files in it, unless the owner
  says so for that session.
- **Don't touch what isn't yours:** another session's worktree, branch, stash, pull request or
  uncommitted files. A session that finds its folder changed under it (a branch it didn't check
  out, files it didn't write) stops, moves to a worktree of its own and tells the owner. It never
  commits, stashes, resets or deletes those changes.
- **Each session updates only its own branches** after a merge. Another session's branch is its
  own to bring up to date.
- **Local resources are a session's own:**
  - a test server listens on a free port chosen when it starts (port 0, or one checked to be
    free), never a fixed one;
  - it is stopped by its process id, never by matching its name (`pkill -f`), which stops the
    other sessions' servers too;
  - test databases, logs, screenshots and coverage data go in the session's scratch folder, never
    in the repository's folder (`rideshare.db` there is nobody's test database);
  - no bare `git stash` or `git stash pop`: the stash is shared by every worktree.
- **After the merge, clean up:** `git worktree remove` the session's worktree and delete its local
  branch. A worktree whose branch is merged and gone is safe to remove; any other one is somebody's
  work.

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
