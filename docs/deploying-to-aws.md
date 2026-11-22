# Deploying to AWS

Infrastructure is Terraform in [infra/](../infra/): one Graviton EC2 server (`t4g.small`: 2 vCPUs,
2 GiB, Ubuntu 24.04) in `eu-north-1` (Stockholm, the AWS project's only allowed Region), running
Docker Compose ([deploy/compose.yaml](../deploy/compose.yaml)), each container with a memory limit:

- **proxy**: Caddy ([deploy/caddy/Caddyfile](../deploy/caddy/Caddyfile)), which handles HTTPS via
  Let's Encrypt once a domain is set, plus rate limits. Searches (`POST /api/match` and
  `/api/requests`) are limited to 30/min per IP and 60/min for all clients together, about what the
  server's one app process can serve (see [Load test](load-test.md)); the two other parts of a
  search (`POST /api/match/transit` and `/api/match/combined`, see
  [The passenger page](passenger-page.md)) to twice that, 60/min per IP and 120/min together;
  `POST /api/auth/link` (each one
  emails a sign-in link) to 10 per 10 min per IP; all writes to 180/min per IP,
  and all of `/api/*` to 1200/min per IP. The per-IP limits are high because phones on mobile
  networks share public IPv4 addresses (carrier-grade NAT): at an event, many people can come from
  one address. Over-limit requests get a 429 response.
- **app**: the FastAPI image built from the root [Dockerfile](../Dockerfile).
- **osrm**: the routing server, see [Routing](routing.md).

The SQLite database lives on a separate EBS volume mounted at `/data`, with daily snapshots kept for
7 days, so it survives redeploys and even replacing the server. There's no load balancer and no SSH:
the server has a fixed Elastic IP that accepts only ports 80/443, and you get a shell through SSM
Session Manager. Expect roughly $18/month: the instance (~$12.60), the IPv4 address (~$3.65), the
disks and snapshots (~$1), the DNS zone ($0.50), the routing images in ECR (~$0.15), and the
uptime alarm ($0.10). The domain itself is billed by Hostpoint. The routing data is built on
GitHub's free arm64 runners (public repository).

Pushes to `main` are tested, built and deployed by
[.github/workflows/deploy.yml](../.github/workflows/deploy.yml):

1. Build arm64 images natively.
2. Push them to ECR.
3. Run [deploy/deploy.sh](../deploy/deploy.sh) on the server via SSM Run Command, with the newest
   routing image in ECR (built separately, see [Routing](routing.md)).

CI signs in to AWS as the `ride-sharing-github-deploy` IAM user, whose access key is stored in GitHub
secrets. Keyless OIDC would be preferable, but the project's managed SCP denies `iam:*Provider*`.

Requires Terraform >= 1.10, the AWS CLI logged in to the target account (`aws login --remote`), and
`gh` logged in with the `workflow` scope.

1. **State bucket** (once per account):
   ```bash
   cd infra/bootstrap && terraform init && terraform apply
   ```
2. **Infrastructure**:
   ```bash
   cd infra
   terraform init -backend-config="bucket=<state_bucket output from step 1>"
   terraform apply
   ```
   The server finishes its first-boot setup (Docker, data volume) a few minutes after `apply`
   returns. The first deploy waits for it.
3. **GitHub settings**: copy `terraform output github_actions_variables` into repo *variables*, and
   create the deploy user's access key directly into repo *secrets*, without printing it:
   ```bash
   aws iam create-access-key --user-name ride-sharing-github-deploy --output json | python3 -c '
   import json, subprocess, sys
   k = json.load(sys.stdin)["AccessKey"]
   subprocess.run(["gh", "secret", "set", "AWS_ACCESS_KEY_ID", "--body", k["AccessKeyId"]], check=True)
   subprocess.run(["gh", "secret", "set", "AWS_SECRET_ACCESS_KEY", "--body", k["SecretAccessKey"]], check=True)'
   ```
   Optional repo variable, not from Terraform: `DEMO_DRIVES`, `on` (or unset) or `off`, the
   [demo drives switch](demo-drives-and-data-retention.md). Change it with
   `gh variable set DEMO_DRIVES --body off`; it takes effect at the next deploy (push to `main` or
   re-run the workflow).
4. **Routing data**: run the *Routing data* workflow from the Actions tab (~10 minutes). It pushes
   the first routing image to ECR, then starts the first deploy. Deploys fail until there is one.
5. **First deploy**: push to `main`, or run the *Test and deploy* workflow from the Actions tab. The
   app is then at `terraform output app_url`, with the demo drives created at startup.

Optional repo variable: `ANDROID_CERT_SHA256`, the Android app's signing-certificate fingerprint(s)
for `/.well-known/assetlinks.json` (see [mobile/android/README.md](../mobile/android/README.md)). Unset,
that file is an empty list.

Optional repo variable: `PHOTO_BUCKET`, the S3 bucket for profile photos, from the
`github_actions_variables` output (see [Profiles](profiles.md)). Unset, the site offers no photos.

Optional repo variable: `EMAIL_NOTIFICATIONS`, `off` to stop the emails about notifications (see
[Notifications by email](email-notifications.md)); unset or `on`, they are sent.
Optional repo variable: `REPORTS_EMAIL`, the address users' reports of a problem are emailed to
(see [Ratings and reports](ratings-and-reports.md)); unset, they are only logged and listed by
`admin.py reports`.

Optional repo **secret**: `CONTACT_EMAIL`, the address the contact form's messages are emailed to
(see [The contact page](contact.md)); unset, the contact page says the form isn't available. A
secret and not a variable, because the deploy log of a public repository shows variables' values.

Optional, for sign-in with Google and Apple: the repo variables `GOOGLE_CLIENT_ID`, `APPLE_CLIENT_ID`,
`APPLE_TEAM_ID`, `APPLE_KEY_ID` and the repo secrets `GOOGLE_CLIENT_SECRET`, `APPLE_PRIVATE_KEY`; see
[Sign-in with Google or Apple](social-login.md#setting-it-up). Without them the buttons aren't shown.

**Domain + HTTPS**: the site runs at <https://rideandrail.ch>. The domain is registered at Hostpoint,
and its DNS is a Route 53 hosted zone managed by Terraform ($0.50/month). To set up a domain:
1. Set `domain_name` in `infra/terraform.tfvars` and run `terraform apply`. This creates the hosted
   zone, plus A records for the apex and `www` pointing at the server, and what sign-in emails need
   (below).
2. At the registrar (Hostpoint control panel → the domain → nameservers), replace the nameservers
   with `terraform output name_servers`.
3. Set the GitHub variables `SITE_ADDRESS`, `EMAIL_FROM` and `PUBLIC_BASE_URL` from
   `terraform output github_actions_variables` and re-run the workflow.

Caddy then obtains Let's Encrypt certificates, redirects HTTP to HTTPS, and redirects `www` to the
apex.

**Sign-in emails** ([infra/email.tf](../infra/email.tf)): with a domain, Terraform creates an Amazon SES
domain identity with Easy DKIM (three CNAME records), a custom MAIL FROM domain `mail.<domain>`
(MX and SPF records), SPF (`include:amazonses.com -all`) and DMARC (`p=reject`) records for the
domain, and lets the server's instance role send from `<email_from_local_part>@<domain>` (default
`no-reply`) and nothing else. The app container reaches the instance role through the metadata
service (hop limit 2, [infra/server.tf](../infra/server.tf)). SES costs $0.10 per 1,000 emails.

SES starts in **sandbox mode**: it only delivers to verified addresses, at most 200 a day. To test
before then, list testers' addresses in `ses_sandbox_test_recipients`; AWS emails each a
confirmation link. For real users, request production access once in the SES console (*Account
dashboard* → *Request production access*), describing the use (transactional sign-in emails only,
low volume); AWS usually answers within a day. Without `EMAIL_FROM` and `PUBLIC_BASE_URL`, the app
sends no links (the API answers 503); viewing as a demo driver still works.

Notes:
- **Downtime per deploy:** the app container is replaced in a few seconds of downtime. The proxy only
  restarts when `deploy/caddy/` changes.
- **Logs:** CloudWatch log group `/ride-sharing`, with streams `app` and `proxy`.
- **Uptime alerts:** with `alert_email` set in `infra/terraform.tfvars`, a Lambda function
  ([infra/health_check/](../infra/health_check/handler.py)) checks `/api/health` every 5 minutes. After
  two failed checks in a row, a CloudWatch alarm emails that address, and emails again on recovery.
  AWS first sends a confirmation link, which must be clicked. Route 53 health checks aren't used
  because their alarms must live in us-east-1, which the AWS project can't use. See
  [infra/monitoring.tf](../infra/monitoring.tf).
- **Shell:** EC2 console → the `ride-sharing` instance → *Connect* → *Session Manager*. Then run
  `sudo -i; cd /opt/ride-sharing; docker compose ps` (or `logs`).
- **OS patches:** Ubuntu installs security updates automatically through unattended-upgrades. Changes
  to [infra/user_data.sh.tftpl](../infra/user_data.sh.tftpl) replace the server on the next apply; the
  data volume is kept.
- **Restoring data:** create a volume from one of the daily snapshots, under EC2 → *Snapshots*.
