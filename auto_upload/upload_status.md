# Upload Status Report

Generated: 2026-08-21 (workflow run 32484399234)

## Last run (Auto Upload workflow, manual dispatch)

- Status: completed/success (exit 0; failed jobs are logged, not fatal)
- 40 jobs processed (MAX_JOBS_PER_RUN=40), 0 uploaded, 40 failed
- Queue: 22,555 total jobs, 22,515 still pending
- Failure (all 40): Facebook OAuthException code 190, subcode 467
  - "Error validating access token: The session is invalid because the user logged out."
  - Earlier log entries show subcode 460 (password changed / session invalidated by Facebook)
- Root cause: Facebook access token in GitHub Secrets (FACEBOOKTOKEN / FACEBOOKDEBUGTOKEN / META_TOKEN_*) is expired/invalid. All 13 FB accounts share this broken token.
- Fix required: generate a fresh long-lived token and update the GitHub secrets, then re-dispatch.

## Platform status

| Platform | Accounts | enabled | Result |
|----------|----------|---------|--------|
| Facebook | 13 (FB-ISR/JPN/KOR/RUS/PH/JIYA/MMR/GLOBAL/BKK/TREND/LTD/CD/NFCD) | Yes | Failing: invalid token (code 190) |
| Instagram | 4 (IG-BKK/TREND/LTD/CD) | No | Not running: accounts disabled, platform_account_id empty (notes: "Resolve Instagram business account ID via Meta API") |
| YouTube | 1 (YT-CD) | No | Not running: no channel ID, OAuth not connected (notes: "Add channel ID and enable after OAuth connection") |

## Publishing Log

- 120 entries, all Facebook, all `failed` on token errors (subcode 460/467).
- No Instagram or YouTube upload history exists.

## Queue breakdown (Publishing Queue via gviz)

- facebook: 22,555 total = 0 uploaded / 40 failed / 22,515 pending
- instagram: 0 jobs (accounts disabled)
- youtube: 0 jobs (account disabled)

## To enable Instagram / YouTube

1. Instagram: resolve each IG business account ID (Meta API `me/accounts` -> instagram_business_account), set `platform_account_id`, set `enabled = Yes`, provide valid `META_TOKEN_IG_*`.
2. YouTube: complete OAuth (secrets `YOUTUBE_CLIENT_SECRET_JSON`, `YOUTUBE_AUTH_CODE`), add channel ID, set `enabled = Yes`.
