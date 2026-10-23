# Deploying NEXORA on AWS (Lambda Function URL)

**Status: not deployed.** This was built and tested locally only. Nothing here has been run against a real AWS account, so check each step in your own account. Architecture: one Lambda function serving both the dashboard and the API through a Function URL. No S3, no API Gateway, no database, no Bedrock.

## Cost check (do this first)

- AWS's Lambda pricing page lists a free tier of 1 million requests and 400,000 GB-seconds per month; it does not itself say whether this is permanent for every account, and it lists no separate Function URL charge. Verify in your Billing console.
- AWS's current free-account description (per an AWS Builder Center article): new accounts on the **Free plan** get credits, are not charged on a card, and the account closes when credits or the plan period run out; Always-Free services such as Lambda do not draw from credits. Terms can change: read the Free Tier page for your account.
- On the Free plan, do **not** enable AWS Organizations, Control Tower or IAM Identity Center (that article says they end the credits and convert the account to Paid).
- Set a billing budget alert before anything else (Billing → Budgets → ₹ or $1 threshold).
- Data transfer and CloudWatch Logs can have costs outside the free allowance; this app logs only the request line.

## Steps (console, about 10 minutes)

1. Build the package on your machine: `python scripts/package_lambda.py` → `dist/nexora-lambda.zip`.
2. AWS Console → Lambda → **Create function** → *Author from scratch*. Name `nexora`, runtime: newest **Python 3.x** offered, architecture x86_64. Keep the default execution role (basic CloudWatch Logs permission only: least privilege, since the code calls no other AWS service).
3. **Code** tab → *Upload from* → .zip file → choose `nexora-lambda.zip`.
4. **Runtime settings → Edit → Handler:** `backend.lambda_handler.handler`
5. **Configuration → General:** memory 256 MB, timeout 15 seconds.
6. **Configuration → Function URL → Create:** Auth type `NONE` for a demo (anyone with the link can use it; it stores nothing, but use only synthetic data) or `AWS_IAM` if you want it locked (then browsers cannot call it without signing). Leave CORS off (same origin).
7. Open the Function URL: the dashboard loads. Click **Load demo data**.
8. Optional cost guard: Configuration → Concurrency → reserve a small value (e.g. 5) if your account's quota allows it.

To update: re-run the package script and upload the zip again.

## Troubleshooting

- `Runtime.ImportModuleError`: the zip must have `backend/`, `frontend/`, `data/` at its top level (the script does this).
- 403 from the URL: check the Function URL auth type and its resource policy.
- Blank page: open the browser console; the CSP only allows same-origin files.

## Things not verified or not done

- A real deployment, Function URL behaviour, and your account's free-tier eligibility.
- Amazon Bedrock: not used. Enabling a model has its own availability and cost rules; if you add it later, send only aggregated, synthetic numbers and label its output as AI-generated.
- Authentication, rate limiting, WAF and audit logging for production use.

## Git: real commit history

Commit as you actually reach each stage, on your machine:

```bash
git init -b main
git add .gitignore README.md docs && git commit -m "Initial project structure and documentation"
git add backend/validation.py && git commit -m "CSV validation"
git add backend/engine.py tests scripts && git commit -m "Analytics, forecast, anomaly rules and tests"
git add backend/app.py backend/server.py frontend data && git commit -m "Dashboard and interactive assumptions"
git add backend/lambda_handler.py && git commit -m "AWS Lambda entry point and packaging"
git remote add origin https://github.com/<you>/nexora.git
git push -u origin main
```

Check `git status` and `git diff --staged` first so no secrets or real data are included. Spread commits over the real working time rather than creating them all at once or with back-dated timestamps.
