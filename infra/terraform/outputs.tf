output "ecr_repository_url" {
  description = "Push the image here (docker push <url>:<tag>)"
  value       = module.ecr.repository_url
}

output "api_function_name" {
  value = module.api.function_name
}

output "api_url" {
  description = "Function URL (IAM auth). BACKEND_URL for Vercel"
  value       = module.api.function_url
}

output "mcp_url" {
  description = "Public MCP endpoint (append mcp/), when enable_public_mcp = true"
  value       = var.enable_public_mcp ? module.mcp[0].function_url : null
}

output "vercel_role_arn" {
  description = "AWS_ROLE_ARN for Vercel"
  value       = module.iam.vercel_role_arn
}

output "github_deploy_role_arn" {
  description = "Repository variable AWS_DEPLOY_ROLE_ARN for the deploy workflow"
  value       = module.iam.github_role_arn
}

output "ssm_parameter_path" {
  description = "Put the secrets here: aws ssm put-parameter --name <path>/NAME --type SecureString --overwrite --value ..."
  value       = local.ssm_path
}

output "guardrail_id" {
  value = module.guardrail.id
}

output "dashboard_url" {
  value = module.observability.dashboard_url
}
