# AWS deployment

This directory provisions a single Amazon Linux 2023 EC2 instance for the
interactive reviewboard app (`reviewbot-web`).

`deploy.sh` will:

- launch an EC2 instance in the selected subnet/VPC
- clone this repo into `/opt/app/ai-reviewer`
- install `.[web]` into a local virtualenv
- copy `reviewbot-web.env` to `/etc/reviewbot/reviewbot-web.env`
- install and enable `reviewbot-web.service`
- start the app on port `8080`

## Usage

1. Copy `reviewbot-web.env.example` to `reviewbot-web.env`.
2. Fill in the GitHub App, GitHub OAuth, and LLM credentials.
3. If you use `GITHUB_PRIVATE_KEY_PATH=/path/to/key.pem` like your local
   launch command, keep that local path in `reviewbot-web.env`.
   `deploy.sh` will copy the PEM to `/etc/reviewbot/github-app.pem` on
   the instance and rewrite the env file to point there.
4. Run `./aws/deploy.sh`.

The instance runs without a public IP. Use the printed private IP over your
private network/VPN, then open `http://<private-ip>:8080`.

If you want the same no-auth behavior as your local command, keep
`DEV_NO_AUTH=1` in `reviewbot-web.env`. On a shared or public host,
remove that and configure `WEB_ALLOWED_USERS` or `WEB_ALLOWED_ORG`.

## HTTPS (nginx + exported ACM cert)

`serge.huggingface.tech` resolves straight to the box's private IP over the
VPN, so TLS is terminated **on the box** by nginx — no load balancer, no DNS
change. The app stays on `8080`; nginx listens on 443 and proxies to it.

The cert is an **exportable ACM public certificate** (publicly trusted and
installable outside AWS). Export it (cert + chain + private key) — if you
used `aws acm export-certificate`, the key comes back passphrase-encrypted,
so decrypt it first:

```bash
openssl rsa -in encrypted-key.pem -out key.pem      # prompts for the passphrase
```

Then push it to the box and bring up nginx:

```bash
CERT_FILE=cert.pem KEY_FILE=key.pem CHAIN_FILE=chain.pem ./aws/setup-tls.sh
```

`setup-tls.sh` uploads the cert/key to `/etc/reviewbot/tls/` (mode `0600`,
root-owned) and runs `provision-tls.sh` on the host, which installs nginx and
writes a 443 vhost (HTTP→HTTPS redirect, SSE-friendly proxy to `8080`). The
instance security group already admits 80/443 from the VPN via its same-SG
rule, so nothing else is needed. Verify with
`curl -sSf https://serge.huggingface.tech/healthz`.

**Renewal:** an exported cert is a static copy — when it renews/expires,
re-export and re-run `setup-tls.sh`; the box won't update itself.

## Updating an existing deployment

`./aws/update.sh` refreshes an already-deployed box in place — no
instance churn, no re-key, no downtime beyond a `systemctl restart`. It:

- reads `.deploy-state.json` to find the instance + key
- refreshes the cached private IP if AWS moved it
- rsyncs the local working tree to `/opt/app/ai-reviewer` (with
  `--delete`, excluding `.git`, `.venv`, `aws/`, caches, and macOS
  noise — so PEMs and local state never leave your machine)
- `pip install -e '.[web]'` to pick up any new dependencies
- rewrites `/etc/reviewbot/${SERVICE_NAME}.env` (and the PEM if your
  `GITHUB_PRIVATE_KEY_PATH` still points at a local file)
- `sudo systemctl restart` the service and prints its status

Use it whenever you've changed code locally or edited
`reviewbot-web.env`. Because it rsyncs your working tree, what's on
the host matches what's on your laptop right now — uncommitted
changes included. If you'd rather ship only what's pushed, commit
+ check out the deployment branch before running.

Destroy the stack with `./aws/destroy.sh`.

## Security notes

- The app listens on `0.0.0.0:8080` plain HTTP and has no public IP;
  clients reach it as `https://serge.huggingface.tech` via nginx on the
  box (see "HTTPS" above), which terminates TLS with the ACM cert. The
  whole box sits inside the VPN. Because the browser↔nginx hop is HTTPS,
  the session cookie's `Secure` flag is correct with `WEB_INSECURE_COOKIES=0`
  — that flag is decoupled from `DEV_NO_AUTH`.
- The exported cert + key live at `/etc/reviewbot/tls/{fullchain,privkey}.pem`,
  mode `0600` root-owned — only root (the nginx master) can read the key.
- `WEB_SESSION_SECRET` must be a real random value. `deploy.sh` and
  `update.sh` mint one with `openssl rand -hex 32` if your env still has
  the example placeholder, and write the value back to
  `reviewbot-web.env` so subsequent updates reuse it.
- The PEM and env files land on the host as `0600` owned by
  `ec2-user` — only the service account can read the GitHub App private
  key, LLM API key, OAuth client secret, and session secret.
- `ALLOW_APPROVE` defaults to off. The web UI relies on this to refuse
  publishing an LLM-chosen `APPROVE` event, because that event is
  influenced by attacker-controlled PR content. Turn it on only after
  deciding your operators will verify every APPROVE before clicking
  publish.
