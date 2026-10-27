# CloudWatch: log groups with short retention, metrics from the app's JSON logs, alarms by email, one dashboard.
# Cost: logs $0.50/GB ingested (demo traffic: cents), the first 10 alarms and 3 dashboards are free.
# Traces of the agents go to Langfuse; CloudWatch covers the platform (errors, latency, throttles, abuse).

variable "name" { type = string }
variable "region" { type = string }
variable "function_names" { type = list(string) }
variable "api_function_name" { type = string }
variable "retention_days" { type = number }
variable "alarm_email" { type = string }
variable "bedrock_model_id" { type = string }
variable "timeout_threshold_ms" { type = number }

locals {
  namespace = "Connoisseur"
  # Events the app logs (structlog JSON, field "event") turned into metrics
  app_events = {
    GuardrailBlocks = "guardrail_blocked"
    QuotaExceeded   = "quota_exceeded"
    ChatFailures    = "chat_failed"
    RequestTimeouts = "request_timeout"
  }
}

resource "aws_cloudwatch_log_group" "function" {
  #checkov:skip=CKV_AWS_158:Logs carry no secrets or raw coordinates (masked in the app); CloudWatch encrypts at rest
  #checkov:skip=CKV_AWS_338:14 days is enough to debug a demo; a year of logs only adds cost
  for_each          = toset(var.function_names)
  name              = "/aws/lambda/${each.value}"
  retention_in_days = var.retention_days
}

resource "aws_cloudwatch_log_metric_filter" "event" {
  for_each       = local.app_events
  name           = "${var.name}-${each.key}"
  log_group_name = aws_cloudwatch_log_group.function[var.api_function_name].name
  pattern        = "{ $.event = \"${each.value}\" }"
  metric_transformation {
    name          = each.key
    namespace     = local.namespace
    value         = "1"
    default_value = "0"
  }
}

# ---------------------------------------------------------------- saved Logs Insights queries (free)
# CloudWatch -> Logs Insights -> Saved queries. Every app line is JSON, so its fields can be queried directly.

locals {
  api_log_group = aws_cloudwatch_log_group.function[var.api_function_name].name
  agent_queries = {
    "agent answers" = <<-EOT
      fields @timestamp, seconds, session_id, trace_id, @message
      | filter event = "chat_done"
      | sort @timestamp desc
      | limit 100
    EOT
    # Replace the session id (shown in the "agent answers" query) to follow one conversation step by step
    "one conversation"    = <<-EOT
      fields @timestamp, level, event, @message
      | filter session_id = "PASTE_SESSION_ID"
      | sort @timestamp asc
    EOT
    "errors and warnings" = <<-EOT
      fields @timestamp, event, error, request_id, @message
      | filter level in ["warning", "error", "critical"] or @message like /Traceback|Failed to export|Task timed out/
      | sort @timestamp desc
      | limit 200
    EOT
    "external tools"      = <<-EOT
      filter event like /tavily|overpass|photon|nominatim|geocode|web_|places/
      | stats count() as calls by event, level
      | sort calls desc
    EOT
    "answer time"         = <<-EOT
      filter event = "chat_done"
      | stats count() as answers, avg(seconds) as avg_s, pct(seconds, 95) as p95_s, max(seconds) as max_s by bin(1h)
    EOT
  }
}

resource "aws_cloudwatch_query_definition" "agent" {
  for_each        = local.agent_queries
  name            = "${var.name}/${each.key}"
  log_group_names = [local.api_log_group]
  query_string    = each.value
}

# ---------------------------------------------------------------- alarms -> email

resource "aws_sns_topic" "alarms" {
  #checkov:skip=CKV_AWS_26:Alarm notifications only; CloudWatch alarms cannot publish to a topic encrypted with the AWS managed key
  count = var.alarm_email == "" ? 0 : 1
  name  = "${var.name}-alarms"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alarms[0].arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

locals {
  alarm_actions = var.alarm_email == "" ? [] : [aws_sns_topic.alarms[0].arn]
  alarms = {
    errors = {
      description = "The API function failed (crash, out of memory or timeout)"
      namespace   = "AWS/Lambda", metric = "Errors", stat = "Sum", threshold = 3, period = 300
      dimensions  = { FunctionName = var.api_function_name }
    }
    throttles = {
      description = "Requests rejected because the concurrency limit was reached"
      namespace   = "AWS/Lambda", metric = "Throttles", stat = "Sum", threshold = 1, period = 300
      dimensions  = { FunctionName = var.api_function_name }
    }
    slow = {
      description = "Answers are close to the function timeout"
      namespace   = "AWS/Lambda", metric = "Duration", stat = "Maximum", threshold = var.timeout_threshold_ms, period = 300
      dimensions  = { FunctionName = var.api_function_name }
    }
    chat_failures = {
      description = "The agent workflow raised an error while answering"
      namespace   = local.namespace, metric = "ChatFailures", stat = "Sum", threshold = 3, period = 300
      dimensions  = {}
    }
    quota_abuse = {
      description = "Many requests over the per-user quota (scraping or a denial-of-wallet attempt)"
      namespace   = local.namespace, metric = "QuotaExceeded", stat = "Sum", threshold = 20, period = 300
      dimensions  = {}
    }
    guardrail_spike = {
      description = "Many guardrail blocks (an attack in progress or a guardrail that is too strict)"
      namespace   = local.namespace, metric = "GuardrailBlocks", stat = "Sum", threshold = 10, period = 300
      dimensions  = {}
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "this" {
  for_each            = local.alarms
  alarm_name          = "${var.name}-${each.key}"
  alarm_description   = each.value.description
  namespace           = each.value.namespace
  metric_name         = each.value.metric
  statistic           = each.value.stat
  period              = each.value.period
  evaluation_periods  = 1
  threshold           = each.value.threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  dimensions          = each.value.dimensions
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

# ---------------------------------------------------------------- dashboard

resource "aws_cloudwatch_dashboard" "this" {
  dashboard_name = var.name
  dashboard_body = jsonencode({
    widgets = [
      {
        type = "metric", x = 0, y = 0, width = 12, height = 6
        properties = {
          title = "API requests and errors", region = var.region, stat = "Sum", period = 300, view = "timeSeries"
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", var.api_function_name],
            [".", "Errors", ".", "."],
            [".", "Throttles", ".", "."],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 0, width = 12, height = 6
        properties = {
          title = "Answer time (ms)", region = var.region, period = 300, view = "timeSeries"
          metrics = [
            ["AWS/Lambda", "Duration", "FunctionName", var.api_function_name, { stat = "p50" }],
            ["...", { stat = "p95" }],
            ["...", { stat = "Maximum" }],
          ]
        }
      },
      {
        type = "metric", x = 0, y = 6, width = 12, height = 6
        properties = {
          title = "Bedrock tokens (${var.bedrock_model_id})", region = var.region, stat = "Sum", period = 3600, view = "timeSeries"
          metrics = [
            ["AWS/Bedrock", "InputTokenCount", "ModelId", var.bedrock_model_id],
            [".", "OutputTokenCount", ".", "."],
          ]
        }
      },
      {
        type = "metric", x = 12, y = 6, width = 12, height = 6
        properties = {
          title   = "Security and abuse signals", region = var.region, stat = "Sum", period = 300, view = "timeSeries"
          metrics = [for metric in keys(local.app_events) : [local.namespace, metric]]
        }
      },
      {
        type = "log", x = 0, y = 12, width = 24, height = 8
        properties = {
          title = "Latest agent answers (trace_id opens the trace in Langfuse)", region = var.region, view = "table"
          query = "SOURCE '${local.api_log_group}' | ${replace(trimspace(local.agent_queries["agent answers"]), "\n", " ")}"
        }
      },
      {
        type = "log", x = 0, y = 20, width = 24, height = 6
        properties = {
          title = "Errors and warnings", region = var.region, view = "table"
          query = "SOURCE '${local.api_log_group}' | ${replace(trimspace(local.agent_queries["errors and warnings"]), "\n", " ")}"
        }
      },
    ]
  })
}

output "log_group_arns" {
  value = [for g in aws_cloudwatch_log_group.function : g.arn]
}

output "log_group_names" {
  value = { for name, g in aws_cloudwatch_log_group.function : name => g.name }
}

output "dashboard_url" {
  value = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards/dashboard/${var.name}"
}
