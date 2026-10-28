# Runbook

How to check, fix and stop the production deployment. Commands assume the default names (`project = connoisseur`, `environment = prod`, region `us-east-1`) and an AWS CLI session with admin rights on the account. Infrastructure setup and teardown are in the [Terraform tutorial](tutorial-terraform-aws.md) (Spanish).

In Git Bash on Windows, prefix AWS commands whose arguments start with `/` with `MSYS_NO_PATHCONV=1`, or the path is rewritten.

## The system at a glance

| Piece | Where | Name |
|---|---|---|
| Web app and BFF | Vercel, project root `web/` | your Vercel project |
| API | Lambda container, Function URL with IAM auth | `connoisseur-prod-api` |
| Public MCP (optional) | Lambda, bearer token | `connoisseur-prod-mcp` |
| Model and guardrail | Bedrock: Nova 2 Lite, guardrail version pinned by Terraform | `terraform output guardrail_id` |
| Quotas and memory | DynamoDB with TTL | `connoisseur-prod-quotas` |
| Secrets | SSM Parameter Store, SecureString | `/connoisseur/prod/{LANGFUSE_PUBLIC_KEY,LANGFUSE_SECRET_KEY,TAVILY_API_KEY,MCP_TOKEN}` |
| Images | ECR, last 10 kept | `connoisseur-prod` |
| Logs, metrics, alarms | CloudWatch, dashboard and saved queries | `connoisseur-prod` |
| Traces | Langfuse Cloud (US) | one trace per message, grouped by session |
| Spend | AWS Budgets, $10 a month, alerts at 50% and 80% actual and 100% forecast | `connoisseur-prod-monthly` |

## Health checks

1. **Open the web app and send a message.** The first one after a quiet period can take 10 to 30 seconds (cold start).
2. **`/health` through IAM** (the URL rejects unsigned calls):

   ```bash
   URL=$(terraform -chdir=infra/terraform output -raw api_url)
   curl -s --aws-sigv4 "aws:amz:us-east-1:lambda" --user "$AWS_ACCESS_KEY_ID:$AWS_SECRET_ACCESS_KEY" \
        ${AWS_SESSION_TOKEN:+-H "x-amz-security-token: $AWS_SESSION_TOKEN"} "${URL}health"
   ```

   Expect `{"status":"ok","llm_provider":"bedrock","vector_index":true,"tracing":true}`. `tracing: false` means the Langfuse keys did not load.
3. **CloudWatch dashboard** `connoisseur-prod`: invocations, errors, duration, guardrail blocks, quota rejections.
4. **Langfuse:** the latest traces should show the graph nodes, the orchestrator's tool calls and the guardrail spans.

## Where to look

CloudWatch → Logs Insights → **Saved queries** (every app log line is JSON):

| Query | Use it for |
|---|---|
| `connoisseur-prod/agent answers` | Latest answers with their time, `session_id` and `trace_id` (open the trace in Langfuse) |
| `connoisseur-prod/one conversation` | Paste a `session_id` to follow one chat step by step |
| `connoisseur-prod/errors and warnings` | Warnings, errors, tracebacks, failed Langfuse exports, Lambda timeouts |
| `connoisseur-prod/external tools` | Calls to Tavily, Overpass, Photon, Nominatim, dish and recipe lookups, orchestrator, by outcome |
| `connoisseur-prod/answer time` | Answers per hour with mean, p95 and max seconds |

## Alarms

Alarms email the address in `alarm_email`. Each links to the metric; start with the "errors and warnings" query.

| Alarm | Fires when (5 min) | First checks |
|---|---|---|
| `connoisseur-prod-errors` | 3+ Lambda errors | Tracebacks in the logs; a bad image (roll back below) |
| `connoisseur-prod-throttles` | Any throttle | Account concurrency quota (10 on new accounts); a traffic burst or a bot |
| `connoisseur-prod-slow` | A request near the timeout | Bedrock latency, Overpass slowness, `request_timeout` events |
| `connoisseur-prod-chat_failures` | 3+ `chat_failed` | The traceback after each `chat_failed` line |
| `connoisseur-prod-quota_abuse` | 20+ quota rejections | `quota_exceeded` lines: one user id hammering the API |
| `connoisseur-prod-guardrail_spike` | 10+ guardrail blocks | An attack, or a guardrail blocking everything (`guardrail_unavailable`) |

## Known problems and fixes

| Symptom | Cause | Fix |
|---|---|---|
| `Failed to export spans batch code: 401`, or `langfuse_auth_failed` at start, `tracing: false` | Langfuse keys wrong, or from another region than `LANGFUSE_HOST` (US vs EU cloud) | Put the right keys in SSM (below) and refresh the function |
| Web searches return nothing; `tavily` lines with 401 | Missing or wrong `TAVILY_API_KEY` | Same as above |
| Tavily quota errors | Free tier is 1,000 credits a month; results are cached a week | Wait for the reset or raise the plan; OpenStreetMap still answers |
| Every web result blocked; `prompt_guard_unavailable` and `guardrail_unavailable` | Prompt Guard cannot load and the guardrail fails closed. Seen once: the baked model files were not readable by the Lambda user | Check the image build step that loads Prompt Guard as a non-root user; roll back to the last good image |
| `web:photon` or `web:tavily` routes instead of `web:osm` | Public Overpass servers are busy; the circuit breaker skips them for 5 minutes | Nothing to do: the fallbacks are designed for this |
| `AccessDeniedException` from Bedrock | The model ID changed without `bedrock_foundation_model`, or model access is not enabled | Set both variables in `terraform.tfvars` and apply; check model access in the Bedrock console |
| `ThrottlingException` from Bedrock | On-demand quota for the model | Retry later; request a quota increase if it persists |
| HTTP 429 in the UI | Per-minute or daily quota for that user | Expected behaviour. Limits: `PER_MINUTE_MESSAGE_LIMIT`, `DAILY_MESSAGE_LIMIT` |
| First message times out in Vercel | Cold start plus a long answer | Retry. The next answers are faster |

## Common operations

### Change a secret

```bash
MSYS_NO_PATHCONV=1 aws ssm put-parameter --name /connoisseur/prod/TAVILY_API_KEY \
    --type SecureString --overwrite --value "<new value>"
```

Secrets are read when an instance starts, so force new instances. Terraform ignores the function description, which makes it a safe thing to change:

```bash
aws lambda update-function-configuration --function-name connoisseur-prod-api \
    --description "secrets refreshed $(date -u +%FT%TZ)"
```

### Deploy

Merging to `main` runs the **Deploy** workflow when `src/`, `scripts/`, the `Dockerfile` or dependencies change: build, Trivy scan, push to ECR, `update-function-code`, signed `/health` smoke test. Run it by hand from the Actions tab (`workflow_dispatch`).

Infrastructure changes (model, orchestrator, limits, alarms) are `terraform plan` and `terraform apply` in `infra/terraform`, by hand.

### Roll back

Images are tagged with the commit SHA and the last 10 are kept:

```bash
aws ecr describe-images --repository-name connoisseur-prod \
    --query 'sort_by(imageDetails,&imagePushedAt)[-5:].[imageTags[0],imagePushedAt]' --output table
aws lambda update-function-code --function-name connoisseur-prod-api \
    --image-uri <account>.dkr.ecr.us-east-1.amazonaws.com/connoisseur-prod:<previous sha>
```

Then revert the commit on `main`, or the next deploy brings the bad image back.

### Switch the model or the orchestrator

In `infra/terraform/terraform.tfvars`, set `bedrock_model_id` and `bedrock_foundation_model` together (IAM grants one model), or `orchestrator_enabled`. Then `terraform apply`. Run the multi-turn suite against the new model first: `python -m evals.run --suite multiturn`.

### A user asks to delete their data

The **Forget my preferences** button calls `DELETE /memory`, which removes the user's saved memory (`mem:user:<id>`) and the current conversation (`conv:user:<id>:<session>`). Conversations expire after 7 days and user memory after 180 days through DynamoDB TTL. No messages or coordinates are stored. Langfuse traces have coordinates, emails and phone numbers masked; delete a user's traces in Langfuse by session if asked.

## Emergency stop

If spend or abuse gets out of hand, stop all invocations without destroying anything:

```bash
aws lambda put-function-concurrency --function-name connoisseur-prod-api --reserved-concurrent-executions 0
```

Lambda then answers every call with HTTP 429, so the web app shows its "message limit" error. To resume:

```bash
aws lambda delete-function-concurrency --function-name connoisseur-prod-api
```

To remove everything: `terraform destroy` as described in the [tutorial](tutorial-terraform-aws.md).

## Costs

October 2026, from Cost Explorer. The figures include the evaluation suites, the model comparison and the red-team runs:

| Service | Cost |
|---|---|
| Amazon Bedrock (model + guardrail) | $1.37 |
| Amazon ECR | $0.02 |
| Lambda, DynamoDB, CloudWatch, SSM, S3 | $0.00 |
| **Total** | **$1.39** |

Vercel Hobby, Langfuse Cloud Hobby and the Tavily free tier cost nothing. At demo traffic a conversation costs about half a cent in Bedrock calls.
