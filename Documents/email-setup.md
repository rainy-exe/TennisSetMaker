# Email setup (setify.net)

SetMaker sends invitations, password-reset links, email confirmations and
"your password/email was changed" notices through one SMTP sender
(`app/mailer.py`). Without the settings below the app still runs: invitations
fail with 502, password reset says plainly that no email was sent, and the
email parts of Settings > My Account are hidden.

The plan this guide follows:

- **Sending:** `noreply@setify.net` through Resend (free: 3,000 emails/month,
  100/day).
- **Receiving:** `support@setify.net` forwarded to a real inbox with Cloudflare
  Email Routing (free). Emails from the app set `Reply-To: support@setify.net`.

## 1. Resend (sending)

1. Create an account at resend.com (use a shared/club address so it can be handed over).
2. **Domains > Add domain** > `setify.net`.
3. Resend lists the DNS records to add (normally an MX and an SPF TXT record
   on the `send` subdomain, and a DKIM TXT record `resend._domainkey`). Add each one in
   Cloudflare **DNS > Records** exactly as shown, with the proxy **off** (grey
   cloud, "DNS only").
4. Back in Resend press **Verify**. It usually takes a few minutes.
5. **API Keys > Create** with "Sending access" for `setify.net` only. Copy it
   once; it is the SMTP password.

These records sit on `send.setify.net`, so they don't clash with the
Email Routing records on the root domain in step 2.

## 2. Cloudflare Email Routing (support inbox)

1. Cloudflare dashboard > `setify.net` > **Email > Email Routing > Get started**.
2. Add the destination inbox (e.g. a Gmail address) and confirm the email
   Cloudflare sends it.
3. **Routing rules > Custom address**: `support` -> that inbox.
4. Let Cloudflare add its MX and SPF records when it asks.

Optional, to reply *as* support@setify.net from Gmail: Gmail **Settings >
Accounts > Send mail as > Add another email address**, SMTP server
`smtp.resend.com`, port 465, username `resend`, password = a Resend API key.

## 3. DMARC

Add one TXT record in Cloudflare DNS:

| Name | Content |
| --- | --- |
| `_dmarc` | `v=DMARC1; p=none; rua=mailto:support@setify.net` |

Gmail and Outlook expect DMARC from senders. Start with `p=none`; once the
reports show only Resend sending for the domain, `p=quarantine` is safer.

## 4. App settings (Coolify > the app > Environment Variables)

```
SMTP_HOST=smtp.resend.com
SMTP_PORT=465
SMTP_USERNAME=resend
SMTP_PASSWORD=<the Resend API key>
SMTP_FROM=Setify <noreply@setify.net>
SMTP_REPLY_TO=support@setify.net
PUBLIC_BASE_URL=https://setify.net
APP_NAME=Setify

SECRET_KEY=<long random value, see below>
SESSION_COOKIE_SECURE=true
CLIENT_IP_HEADER=CF-Connecting-IP
```

- `SECRET_KEY` signs logins and every emailed link. Set it once to a long random
  value (`python -c "import secrets; print(secrets.token_urlsafe(48))"`) and keep
  it. Changing it logs everyone out and kills links already sent. Never use
  the development default in production.
- `PUBLIC_BASE_URL` is the address links in emails point to (no trailing path).
- `SESSION_COOKIE_SECURE=true` only once the site is served over HTTPS.
- `CLIENT_IP_HEADER=CF-Connecting-IP` lets the log-in and reset limits see each
  visitor's real IP. Only set it while the DNS record is proxied through
  Cloudflare (orange cloud); otherwise leave it out.
- `APP_NAME` is the product name used in email subjects and text (default
  `SetMaker`).
- Port 587 with STARTTLS also works (`SMTP_PORT=587`, or set
  `SMTP_SECURITY=starttls`) if the server can't reach port 465.

Mark `SMTP_PASSWORD` and `SECRET_KEY` as secrets in Coolify. Redeploy after
changing them.

## 5. Database

This change adds three columns to `users`. Run the migration on deploy (back up
the database first):

```bash
python -m flask --app run.py db upgrade
```

## 6. Check it works

1. Log in, open **Settings > My Account** and press **Send confirmation email**.
2. Log out, use **Forgot password?** with your address and complete the reset.
3. Invite a test member.
4. In Gmail, open a received email > **Show original**: SPF, DKIM and DMARC
   should all say PASS. If an email lands in spam, check this first.

Resend's dashboard (**Emails**) shows each send and whether it was delivered or
bounced.

## How the flows work

| Flow | Link lifetime | Notes |
| --- | --- | --- |
| Password reset | 1 hour, works once | Logs out every device; sends a "password changed" notice. Same reply whether or not the account exists. |
| Confirm email | 3 days | Optional; accounts work unconfirmed. Resend from My Account. Accepting an invitation or resetting a password also confirms it. |
| Change email | 24 hours | Needs the current password. The approval link goes to the **current** address; nothing changes until "Approve change" is pressed there. The new address then gets a normal confirmation link and starts unconfirmed. |
| Change password | n/a | Needs the current password; other devices are logged out. |

Limits: 10 failed log-ins per email per visitor in 15 minutes; one account
email per user per minute; 5 reset emails per address and 30 reset requests per
visitor per hour. Counts are kept in memory, per worker process, and reset on
restart.

## Handover

Create the Resend account with an address the club controls (or add the
client as a team member), the same as for the Cloudflare and Contabo accounts.
Domains bought recently can't be transferred to another registrar for 60 days,
but adding another person as a member of the Cloudflare account works straight
away.
