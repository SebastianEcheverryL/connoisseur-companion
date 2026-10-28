# Connoisseur Companion on AWS: one Lambda (container) behind a Function URL with IAM auth, called only
# by the Vercel frontend. Bedrock for the LLM and guardrails, DynamoDB for quotas, SSM for secrets.

data "aws_caller_identity" "current" {}

locals {
  name       = "${var.project}-${var.environment}"
  account_id = data.aws_caller_identity.current.account_id
  # Built from names (not module outputs) so IAM and the function do not depend on each other
  api_function_name = "${local.name}-api"
  mcp_function_name = "${local.name}-mcp"
  api_function_arn  = "arn:aws:lambda:${var.region}:${local.account_id}:function:${local.api_function_name}"
  mcp_function_arn  = "arn:aws:lambda:${var.region}:${local.account_id}:function:${local.mcp_function_name}"
  ssm_path          = "/${var.project}/${var.environment}"
  function_names    = var.enable_public_mcp ? [local.api_function_name, local.mcp_function_name] : [local.api_function_name]
}

module "ecr" {
  source = "./modules/ecr"
  name   = local.name
}

module "dynamodb" {
  source = "./modules/dynamodb"
  name   = "${local.name}-quotas"
}

module "ssm" {
  source = "./modules/ssm"
  path   = local.ssm_path
  # Values are set by hand (aws ssm put-parameter), never in Terraform files or state
  names = ["LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "TAVILY_API_KEY", "MCP_TOKEN"]
}

module "guardrail" {
  source = "./modules/bedrock_guardrail"
  name   = local.name
}

module "observability" {
  source               = "./modules/observability"
  name                 = local.name
  region               = var.region
  function_names       = local.function_names
  api_function_name    = local.api_function_name
  retention_days       = var.log_retention_days
  alarm_email          = var.alarm_email
  bedrock_model_id     = var.bedrock_model_id
  timeout_threshold_ms = var.lambda_timeout_s * 1000 * 0.8
}

module "iam" {
  source                      = "./modules/iam"
  name                        = local.name
  region                      = var.region
  account_id                  = local.account_id
  log_group_arns              = module.observability.log_group_arns
  bedrock_model_id            = var.bedrock_model_id
  bedrock_foundation_model    = var.bedrock_foundation_model
  guardrail_arn               = module.guardrail.arn
  quota_table_arn             = module.dynamodb.arn
  ssm_path                    = local.ssm_path
  ecr_repository_arn          = module.ecr.arn
  api_function_arn            = local.api_function_arn
  deployable_function_arns    = var.enable_public_mcp ? [local.api_function_arn, local.mcp_function_arn] : [local.api_function_arn]
  vercel_team_slug            = var.vercel_team_slug
  vercel_project_name         = var.vercel_project_name
  github_repository           = var.github_repository
  create_github_oidc_provider = var.create_github_oidc_provider
  github_oidc_sub_prefix      = var.github_oidc_sub_prefix
}

locals {
  common_env = {
    LLM_PROVIDER              = "bedrock"
    BEDROCK_MODEL_ID          = var.bedrock_model_id
    GUARDRAIL_PROVIDER        = "bedrock"
    BEDROCK_GUARDRAIL_ID      = module.guardrail.id
    BEDROCK_GUARDRAIL_VERSION = module.guardrail.version
    GUARDRAIL_FAIL_CLOSED     = "true"
    SSM_PARAMETER_PATH        = "${local.ssm_path}/"
    LANGFUSE_HOST             = var.langfuse_host
    OSM_USER_AGENT            = var.osm_user_agent
    QUOTA_TABLE               = module.dynamodb.name
    STATE_TABLE               = module.dynamodb.name
    DAILY_MESSAGE_LIMIT       = tostring(var.daily_message_limit)
    PER_MINUTE_MESSAGE_LIMIT  = tostring(var.per_minute_message_limit)
    ORCHESTRATOR_ENABLED      = tostring(var.orchestrator_enabled)
  }
}

module "api" {
  source               = "./modules/lambda"
  function_name        = local.api_function_name
  role_arn             = module.iam.lambda_role_arn
  image_uri            = "${module.ecr.repository_url}:${var.image_tag}"
  memory_mb            = var.lambda_memory_mb
  timeout_s            = var.lambda_timeout_s
  reserved_concurrency = var.reserved_concurrency
  log_group_name       = module.observability.log_group_names[local.api_function_name]
  url_auth_type        = "AWS_IAM" # only callers with lambda:InvokeFunctionUrl (the Vercel role) get in
  environment = merge(local.common_env, {
    # The Function URL already authenticated the caller with IAM: the user id it forwards can be trusted
    TRUST_USER_HEADER = "true"
  })
}

module "mcp" {
  count                = var.enable_public_mcp ? 1 : 0
  source               = "./modules/lambda"
  function_name        = local.mcp_function_name
  role_arn             = module.iam.lambda_role_arn
  image_uri            = "${module.ecr.repository_url}:${var.image_tag}"
  memory_mb            = 1024
  timeout_s            = 60
  reserved_concurrency = var.reserved_concurrency
  log_group_name       = module.observability.log_group_names[local.mcp_function_name]
  url_auth_type        = "NONE" # MCP clients cannot sign SigV4: the app requires MCP_TOKEN instead
  environment = merge(local.common_env, {
    EXPOSE_CHAT = "false" # /chat and /feedback answer 404 here: only /mcp and /health
  })
}

module "budget" {
  count       = var.alarm_email == "" ? 0 : 1
  source      = "./modules/budgets"
  name        = local.name
  limit_usd   = var.monthly_budget_usd
  alarm_email = var.alarm_email
}
