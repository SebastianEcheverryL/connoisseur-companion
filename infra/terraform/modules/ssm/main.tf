# Secrets as SSM SecureString parameters (standard tier: free; encrypted with the AWS managed aws/ssm key).
# Terraform creates each parameter with a placeholder and then ignores its value, so the real secret is
# set once by hand and never appears in .tf files, tfvars or the Terraform state:
#   aws ssm put-parameter --name /connoisseur/prod/TAVILY_API_KEY --type SecureString --overwrite --value ...
# The app reads every parameter under the path at cold start and skips placeholders.

variable "path" {
  description = "Parameter path without trailing slash, e.g. /connoisseur/prod"
  type        = string
}

variable "names" {
  type = list(string)
}

resource "aws_ssm_parameter" "secret" {
  #checkov:skip=CKV_AWS_337:Encrypted with the AWS managed aws/ssm key; a customer key adds $1/month per key
  for_each = toset(var.names)
  name     = "${var.path}/${each.value}"
  type     = "SecureString"
  value    = "CHANGE_ME"

  lifecycle {
    ignore_changes = [value]
  }
}

output "arns" {
  value = [for p in aws_ssm_parameter.secret : p.arn]
}
