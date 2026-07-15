# Credential Rotation & Hardening — operator runbook

These steps touch **live secrets** and must be run by the repository owner — they are intentionally
NOT automated. They close the credential-hygiene gaps flagged in `PRE-PUBLISH.md` and SECURITY.md's
deliberate trade-off #5 (`GH_PAT` as a classic PAT).

> Do these before any public release and whenever a credential may have been exposed. After each
> rotation, run one end-to-end pipeline to confirm the new credential works before revoking the old.

## 1. The PAT that leaked into `.git/config`

`PRE-PUBLISH.md` recorded a live `ghp_…` token embedded in the `origin` remote URL. Even though it is
not in published content, it is a real credential on disk.

```bash
# 1. See whether the remote URL still embeds a token (it should be a plain https URL).
git remote -v | sed -E 's#https://[^@]*@#https://***@#'

# 2. If it embeds one, re-point the remote to the token-less URL:
git remote set-url origin https://github.com/<owner>/<repo>.git

# 3. Revoke the exposed token: GitHub → Settings → Developer settings → Personal access tokens →
#    find the token → Revoke. Then create a fresh one (see §2) and store it ONLY in the places below.
```

Auth for git operations should come from the credential helper / gh, never a token baked into the URL
(the Medic already pushes via ambient credentials — commit `f1e6bbb`).

## 2. `GH_PAT` (classic) → GitHub App installation token — the real fix

SECURITY.md #5: the built-in `GITHUB_TOKEN` lacks the `workflow` scope and its pushes do not
re-trigger workflows, so the agent uses a classic PAT. The production posture is a **GitHub App**:

1. Create a GitHub App (org or personal): **Settings → Developer settings → GitHub Apps → New**.
2. Permissions: `Contents: Read & write`, `Workflows: Read & write`, `Actions: Read`. No user scope.
3. Install it on this repo only. Note the **App ID** and generate a **private key** (`.pem`).
4. In CI, mint a short-lived installation token per run (e.g. `actions/create-github-app-token@v1`)
   and use it where `GH_PAT` is used today (`run_agent.yml` checkout, `push_to_github`).
5. Store `APP_ID` + `APP_PRIVATE_KEY` as repo **Secrets**; delete the `GH_PAT` secret once green.

Benefit: tokens are short-lived and scoped to this repo, not a long-lived account-wide PAT.

## 3. `.env` on disk → a secrets manager

`.env` currently holds live AWS / Databricks / OpenAI / Pinecone / DB credentials in plaintext.

- **Minimum:** ensure `.env` is git-ignored (it is), `chmod 600 .env`, and never commit it.
- **Better (local):** use `direnv` + a vault (1Password CLI `op run`, `aws-vault`, or `sops`-encrypted
  `.env`) so secrets are decrypted into the process env only for the command that needs them.
- **CI already does this correctly:** GitHub Secrets/Variables, injected per job. No change needed.

## 4. Rotation cadence

| Credential | Rotate | How |
|---|---|---|
| GitHub App key | on exposure / every 90d | regenerate `.pem`, update `APP_PRIVATE_KEY` secret |
| AWS runtime keys (`self-healing-agent-svc`) | 90d | IAM → create new access key → update `.env` + `AWS_ACCESS_KEY_ID/SECRET` secrets → delete old |
| Databricks SP secret | 90d | rotate the service-principal OAuth secret → update `DATABRICKS_CLIENT_SECRET` |
| DB passwords | on exposure | rotate at the bootstrap (`random_password`), re-publish to SSM / GitHub Secrets |
| OpenAI / Pinecone keys | on exposure | regenerate in the provider console → update `.env` + secrets |

## 5. Verify nothing regressed

```bash
gitleaks detect --source . --redact   # full-history secret scan (also runs in security.yml)
make lint && make test                # hermetic suite
```
