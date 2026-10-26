# The API as a container-image Lambda with a Function URL that streams responses (server-sent events).
# Cost: free tier covers 400,000 GB-seconds a month; beyond that ~$0.0000167 per GB-second.
# At 2 GB and ~30 s per answer, 1,000 answers ≈ 60,000 GB-s, inside the free tier.

variable "function_name" { type = string }
variable "role_arn" { type = string }
variable "image_uri" { type = string }
variable "memory_mb" { type = number }
variable "timeout_s" { type = number }
variable "reserved_concurrency" { type = number }
variable "log_group_name" { type = string }
variable "environment" { type = map(string) }

variable "url_auth_type" {
  description = "AWS_IAM (signed callers only) or NONE (public; the app must authenticate)"
  type        = string
  validation {
    condition     = contains(["AWS_IAM", "NONE"], var.url_auth_type)
    error_message = "url_auth_type must be AWS_IAM or NONE."
  }
}

resource "aws_lambda_function" "this" {
  #checkov:skip=CKV_AWS_116:Synchronous Function URL invocations: a dead-letter queue only applies to async events
  #checkov:skip=CKV_AWS_117:Calls only public endpoints (Bedrock, OSM, Tavily, Langfuse); a VPC would need a NAT gateway (~$32/month)
  #checkov:skip=CKV_AWS_272:Code signing is not supported for container images; images are immutable and scanned
  #checkov:skip=CKV_AWS_50:Request tracing is done with Langfuse (OpenTelemetry), not X-Ray
  #checkov:skip=CKV_AWS_173:Environment holds no secrets (they are read from SSM at start)
  function_name                  = var.function_name
  role                           = var.role_arn
  package_type                   = "Image"
  image_uri                      = var.image_uri
  architectures                  = ["x86_64"]
  memory_size                    = var.memory_mb
  timeout                        = var.timeout_s
  reserved_concurrent_executions = var.reserved_concurrency

  # /tmp holds the copy of the vector index and the HTTP cache
  ephemeral_storage {
    size = 1024
  }

  environment {
    variables = var.environment
  }

  logging_config {
    log_format = "Text" # the app already writes one JSON object per line
    log_group  = var.log_group_name
  }

  lifecycle {
    # New images are deployed by CI (aws lambda update-function-code): Terraform must not roll them back.
    # The description is changed by hand to force a cold start that reloads the SSM secrets.
    ignore_changes = [image_uri, description]
  }
}

resource "aws_lambda_function_url" "this" {
  #checkov:skip=CKV_AWS_258:NONE only for the optional public MCP function, which requires a bearer MCP_TOKEN in the app
  function_name      = aws_lambda_function.this.function_name
  authorization_type = var.url_auth_type
  invoke_mode        = "RESPONSE_STREAM" # SSE events reach the browser while the agents work
}

# A public URL needs an explicit resource policy for anonymous callers
resource "aws_lambda_permission" "public_url" {
  #checkov:skip=CKV_AWS_301:Optional public MCP function, protected by a bearer MCP_TOKEN in the app
  count                  = var.url_auth_type == "NONE" ? 1 : 0
  statement_id           = "PublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.this.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "public_invoke_via_url" {
  #checkov:skip=CKV_AWS_301:Optional public MCP function, invocable only through its URL
  count                    = var.url_auth_type == "NONE" ? 1 : 0
  statement_id             = "PublicInvokeOnlyViaFunctionUrl"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.this.function_name
  principal                = "*"
  invoked_via_function_url = true
}

output "function_name" {
  value = aws_lambda_function.this.function_name
}

output "function_url" {
  value = aws_lambda_function_url.this.function_url
}
