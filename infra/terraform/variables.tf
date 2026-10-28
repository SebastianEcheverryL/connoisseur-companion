variable "project" {
  description = "Prefix for every resource name"
  type        = string
  default     = "connoisseur"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "region" {
  description = "Region for every resource. Nova Lite's us. inference profile serves us-east-1, us-east-2 and us-west-2"
  type        = string
  default     = "us-east-1"
}

# ---------- container image and function

variable "image_tag" {
  description = "Tag of the image in ECR used when the function is created. Later deploys update the code from CI (or aws lambda update-function-code), not from Terraform"
  type        = string
  default     = "bootstrap"
}

variable "lambda_memory_mb" {
  description = "Memory (and proportional CPU) for the API function. Embeddings and ONNX guardrails are CPU bound"
  type        = number
  default     = 2048
}

variable "lambda_timeout_s" {
  description = "Hard limit per request. The app gives up at 240 s and answers with a friendly message"
  type        = number
  default     = 300
}

variable "reserved_concurrency" {
  description = "Max concurrent executions (denial-of-wallet cap). -1 = no reservation: new accounts have a 10-execution quota and cannot reserve any until it is raised"
  type        = number
  default     = -1
}

# ---------- models and app behaviour

variable "bedrock_model_id" {
  description = "Model or inference profile the agents call (Converse API)"
  type        = string
  default     = "us.amazon.nova-lite-v1:0"
}

variable "bedrock_foundation_model" {
  description = "Foundation model behind bedrock_model_id, needed in the IAM policy of a cross-region inference profile"
  type        = string
  default     = "amazon.nova-lite-v1:0"
}

variable "orchestrator_enabled" {
  description = "Let an LLM choose the tools for each message instead of the fixed routing (needs a model with reliable tool use, e.g. Nova 2 Lite)"
  type        = bool
  default     = false
}

variable "daily_message_limit" {
  type    = number
  default = 30
}

variable "per_minute_message_limit" {
  type    = number
  default = 6
}

variable "langfuse_host" {
  description = "Langfuse Cloud region URL (keys go to SSM, not here)"
  type        = string
  default     = "https://cloud.langfuse.com"
}

variable "osm_user_agent" {
  description = "Nominatim's usage policy requires an identifying User-Agent with a contact URL"
  type        = string
  default     = "connoisseur-companion/0.2 (+https://github.com/SebastianEcheverryL/connoisseur-companion)"
}

variable "enable_public_mcp" {
  description = "Create a second function (same image) that serves only /mcp on a public URL, protected by MCP_TOKEN"
  type        = bool
  default     = false
}

# ---------- callers: Vercel (frontend) and GitHub Actions (deploys)

variable "vercel_team_slug" {
  description = "Vercel team slug for OIDC federation (Settings > General). Empty = no Vercel role"
  type        = string
  default     = ""
}

variable "vercel_project_name" {
  type    = string
  default = "connoisseur-companion"
}

variable "github_repository" {
  description = "owner/repo allowed to deploy from its main branch. Empty = no GitHub role"
  type        = string
  default     = "SebastianEcheverryL/connoisseur-companion"
}

variable "create_github_oidc_provider" {
  description = "An account can have only one GitHub OIDC provider: set false if it already exists"
  type        = bool
  default     = true
}

variable "github_oidc_sub_prefix" {
  description = "sub_claim_prefix from `gh api repos/OWNER/REPO/actions/oidc/customization/sub` when the repo uses immutable subjects. Empty = repo:owner/repo"
  type        = string
  default     = ""
}

# ---------- cost and alerts

variable "alarm_email" {
  description = "Email for alarms and budget alerts (confirm the SNS subscription email). Empty = no notifications"
  type        = string
  default     = ""
}

variable "monthly_budget_usd" {
  type    = number
  default = 10
}

variable "log_retention_days" {
  type    = number
  default = 14
}
