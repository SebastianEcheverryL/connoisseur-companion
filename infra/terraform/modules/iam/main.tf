# Three roles, each with only what it needs (IAM itself is free):
#  - lambda:  what the function may call (logs, Bedrock model + guardrail, quota table, its SSM path)
#  - vercel:  assumed by the Vercel frontend through OIDC; may only invoke the API Function URL
#  - github:  assumed by GitHub Actions on main through OIDC; may push images and update the functions' code
# No long-lived access keys anywhere.

variable "name" { type = string }
variable "region" { type = string }
variable "account_id" { type = string }
variable "log_group_arns" { type = list(string) }
variable "bedrock_model_id" { type = string }
variable "bedrock_foundation_model" { type = string }
variable "guardrail_arn" { type = string }
variable "quota_table_arn" { type = string }
variable "ssm_path" { type = string }
variable "ecr_repository_arn" { type = string }
variable "api_function_arn" { type = string }
variable "deployable_function_arns" { type = list(string) }
variable "vercel_team_slug" { type = string }
variable "vercel_project_name" { type = string }
variable "github_repository" { type = string }
variable "create_github_oidc_provider" { type = bool }
variable "github_oidc_sub_prefix" { type = string }

locals {
  # A cross-region inference profile (us.*) routes to several regions: allow the profile and the
  # foundation model it points to, in any region the profile may use
  is_profile = can(regex("^(us|eu|apac|global)\\.", var.bedrock_model_id))
  bedrock_resources = local.is_profile ? [
    "arn:aws:bedrock:*:${var.account_id}:inference-profile/${var.bedrock_model_id}",
    "arn:aws:bedrock:*::foundation-model/${var.bedrock_foundation_model}",
  ] : ["arn:aws:bedrock:${var.region}::foundation-model/${var.bedrock_model_id}"]
}

# ---------------------------------------------------------------- Lambda execution role

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${var.name}-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "lambda" {
  statement {
    sid       = "WriteOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [for arn in var.log_group_arns : "${arn}:*"]
  }
  statement {
    sid       = "InvokeOneModel"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = local.bedrock_resources
  }
  statement {
    sid       = "ApplyOwnGuardrail"
    actions   = ["bedrock:ApplyGuardrail"]
    resources = [var.guardrail_arn]
  }
  statement {
    sid       = "CountQuotas"
    actions   = ["dynamodb:UpdateItem"]
    resources = [var.quota_table_arn]
  }
  # Conversation and user memory live in the same table (keys conv:* and mem:*)
  statement {
    sid       = "ReadWriteMemory"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
    resources = [var.quota_table_arn]
  }
  statement {
    sid = "ReadOwnSecrets"
    # Decryption with the AWS managed aws/ssm key needs no extra KMS permission.
    # IAM matches the path exactly as requested, and the app requests it with a trailing slash.
    actions = ["ssm:GetParametersByPath"]
    resources = [
      "arn:aws:ssm:${var.region}:${var.account_id}:parameter${trimsuffix(var.ssm_path, "/")}",
      "arn:aws:ssm:${var.region}:${var.account_id}:parameter${trimsuffix(var.ssm_path, "/")}/",
    ]
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "least-privilege"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

# ---------------------------------------------------------------- Vercel (frontend backend-for-frontend)

locals {
  vercel_enabled = var.vercel_team_slug != ""
  vercel_issuer  = "oidc.vercel.com/${var.vercel_team_slug}"
}

resource "aws_iam_openid_connect_provider" "vercel" {
  count          = local.vercel_enabled ? 1 : 0
  url            = "https://${local.vercel_issuer}"
  client_id_list = ["https://vercel.com/${var.vercel_team_slug}"]
}

data "aws_iam_policy_document" "vercel_trust" {
  count = local.vercel_enabled ? 1 : 0
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.vercel[0].arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.vercel_issuer}:aud"
      values   = ["https://vercel.com/${var.vercel_team_slug}"]
    }
    # Only the production deployments of this one project
    condition {
      test     = "StringEquals"
      variable = "${local.vercel_issuer}:sub"
      values   = ["owner:${var.vercel_team_slug}:project:${var.vercel_project_name}:environment:production"]
    }
  }
}

resource "aws_iam_role" "vercel" {
  count                = local.vercel_enabled ? 1 : 0
  name                 = "${var.name}-vercel"
  assume_role_policy   = data.aws_iam_policy_document.vercel_trust[0].json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "vercel" {
  statement {
    sid       = "InvokeApiUrl"
    actions   = ["lambda:InvokeFunctionUrl"]
    resources = [var.api_function_arn]
    condition {
      test     = "StringEquals"
      variable = "lambda:FunctionUrlAuthType"
      values   = ["AWS_IAM"]
    }
  }
  statement {
    sid       = "InvokeApiOnlyThroughUrl"
    actions   = ["lambda:InvokeFunction"]
    resources = [var.api_function_arn]
    condition {
      test     = "Bool"
      variable = "lambda:InvokedViaFunctionUrl"
      values   = ["true"]
    }
  }
}

resource "aws_iam_role_policy" "vercel" {
  count  = local.vercel_enabled ? 1 : 0
  name   = "invoke-api-url"
  role   = aws_iam_role.vercel[0].id
  policy = data.aws_iam_policy_document.vercel.json
}

# ---------------------------------------------------------------- GitHub Actions (deploys from main)

locals {
  github_enabled = var.github_repository != ""
  github_issuer  = "token.actions.githubusercontent.com"
  # Repositories with immutable subjects send owner@id/repo@id instead of owner/repo
  github_sub_prefix = var.github_oidc_sub_prefix != "" ? var.github_oidc_sub_prefix : "repo:${var.github_repository}"
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = local.github_enabled && var.create_github_oidc_provider ? 1 : 0
  url            = "https://${local.github_issuer}"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = local.github_enabled && !var.create_github_oidc_provider ? 1 : 0
  url   = "https://${local.github_issuer}"
}

locals {
  github_provider_arn = local.github_enabled ? (
    var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
  ) : null
}

data "aws_iam_policy_document" "github_trust" {
  count = local.github_enabled ? 1 : 0
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.github_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.github_issuer}:aud"
      values   = ["sts.amazonaws.com"]
    }
    # Only workflows running on the main branch of this repository
    condition {
      test     = "StringEquals"
      variable = "${local.github_issuer}:sub"
      values   = ["${local.github_sub_prefix}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "github" {
  count              = local.github_enabled ? 1 : 0
  name               = "${var.name}-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_trust[0].json
}

data "aws_iam_policy_document" "github" {
  #checkov:skip=CKV_AWS_107:Registry login tokens (ECR push, ECR Public pull) for a role that only main of this repo can assume
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # this action has no resource-level permissions
  }
  # Authenticated pulls of public base images: anonymous pulls from shared runners hit the ECR Public data limit
  statement {
    sid       = "EcrPublicLogin"
    actions   = ["ecr-public:GetAuthorizationToken", "sts:GetServiceBearerToken"]
    resources = ["*"] # these actions have no resource-level permissions
  }
  statement {
    sid = "PushImages"
    actions = [
      "ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:CompleteLayerUpload", "ecr:DescribeImages",
      "ecr:InitiateLayerUpload", "ecr:PutImage", "ecr:UploadLayerPart",
    ]
    resources = [var.ecr_repository_arn]
  }
  statement {
    sid       = "UpdateFunctionCode"
    actions   = ["lambda:UpdateFunctionCode", "lambda:GetFunction", "lambda:GetFunctionConfiguration"]
    resources = var.deployable_function_arns
  }
  # The smoke test looks up the Function URL and calls GET /health on it
  statement {
    sid       = "SmokeTestUrlLookup"
    actions   = ["lambda:GetFunctionUrlConfig"]
    resources = [var.api_function_arn]
  }
  statement {
    sid       = "SmokeTestApiUrl"
    actions   = ["lambda:InvokeFunctionUrl"]
    resources = [var.api_function_arn]
    condition {
      test     = "StringEquals"
      variable = "lambda:FunctionUrlAuthType"
      values   = ["AWS_IAM"]
    }
  }
  statement {
    sid       = "SmokeTestOnlyThroughUrl"
    actions   = ["lambda:InvokeFunction"]
    resources = [var.api_function_arn]
    condition {
      test     = "Bool"
      variable = "lambda:InvokedViaFunctionUrl"
      values   = ["true"]
    }
  }
}

resource "aws_iam_role_policy" "github" {
  count  = local.github_enabled ? 1 : 0
  name   = "deploy-image"
  role   = aws_iam_role.github[0].id
  policy = data.aws_iam_policy_document.github.json
}

output "lambda_role_arn" {
  value = aws_iam_role.lambda.arn
}

output "vercel_role_arn" {
  value = local.vercel_enabled ? aws_iam_role.vercel[0].arn : null
}

output "github_role_arn" {
  value = local.github_enabled ? aws_iam_role.github[0].arn : null
}
