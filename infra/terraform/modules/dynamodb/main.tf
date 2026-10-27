# Per-user quota counters (messages per minute and per day) and memory: conversation state (conv:*, 7 days)
# and the preferences of signed-in users (mem:*, 180 days, deleted by "forget my preferences").
# On-demand billing: a few cents a month at demo traffic, $0 when idle. Items expire on their own through
# DynamoDB TTL (free).

variable "name" {
  type = string
}

resource "aws_dynamodb_table" "quotas" {
  #checkov:skip=CKV_AWS_119:Counters and short-lived preferences keyed by a GitHub user id, with TTL; AWS owned key
  #checkov:skip=CKV_AWS_28:Counters and memory expire on their own; point-in-time recovery would only add cost
  name         = var.name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"

  attribute {
    name = "pk"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  server_side_encryption {
    enabled = true # AWS owned key: no KMS cost
  }

  # Counters and memory that expire on their own: backups would only add cost
  point_in_time_recovery {
    enabled = false
  }
}

output "name" {
  value = aws_dynamodb_table.quotas.name
}

output "arn" {
  value = aws_dynamodb_table.quotas.arn
}
