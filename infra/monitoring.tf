# Uptime alerts by email. Every 5 minutes a small Lambda function requests the
# site's /api/health (health_check/handler.py) and fails unless it answers
# "ok". Two failed checks in a row, or no check running at all, put the alarm
# in ALARM and email alert_email; recovery sends another email.
#
# Route 53 health checks would be simpler, but their metrics, and so their
# alarms, live in us-east-1, which this AWS project can't use.
#
# Off while alert_email is empty. AWS emails a confirmation link to the
# address first; no alerts arrive until it's clicked.

locals {
  monitoring_enabled = var.alert_email != ""
  health_check_name  = "${var.project}-health-check"
  health_url = local.https_enabled ? "https://${var.domain_name}/api/health" : (
    "http://${aws_eip.app.public_ip}/api/health"
  )
}

data "archive_file" "health_check" {
  type        = "zip"
  source_file = "${path.module}/health_check/handler.py"
  output_path = "${path.module}/.build/health_check.zip"
}

resource "aws_lambda_function" "health_check" {
  count = local.monitoring_enabled ? 1 : 0

  function_name    = local.health_check_name
  role             = aws_iam_role.health_check[0].arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "handler.handler"
  filename         = data.archive_file.health_check.output_path
  source_code_hash = data.archive_file.health_check.output_base64sha256
  memory_size      = 128
  timeout          = 20

  environment {
    variables = {
      HEALTH_URL = local.health_url
    }
  }

  depends_on = [aws_cloudwatch_log_group.health_check]
}

# A failed check is the signal: don't retry it.
resource "aws_lambda_function_event_invoke_config" "health_check" {
  count = local.monitoring_enabled ? 1 : 0

  function_name          = aws_lambda_function.health_check[0].function_name
  maximum_retry_attempts = 0
}

resource "aws_cloudwatch_log_group" "health_check" {
  count = local.monitoring_enabled ? 1 : 0

  name              = "/aws/lambda/${local.health_check_name}"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "health_check" {
  count = local.monitoring_enabled ? 1 : 0

  name               = local.health_check_name
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy" "health_check_logs" {
  count = local.monitoring_enabled ? 1 : 0

  name = "logs"
  role = aws_iam_role.health_check[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.health_check[0].arn}:*"
    }]
  })
}

resource "aws_cloudwatch_event_rule" "health_check" {
  count = local.monitoring_enabled ? 1 : 0

  name                = local.health_check_name
  description         = "Check ${local.health_url} every 5 minutes"
  schedule_expression = "rate(5 minutes)"
}

resource "aws_cloudwatch_event_target" "health_check" {
  count = local.monitoring_enabled ? 1 : 0

  rule = aws_cloudwatch_event_rule.health_check[0].name
  arn  = aws_lambda_function.health_check[0].arn
}

resource "aws_lambda_permission" "health_check_schedule" {
  count = local.monitoring_enabled ? 1 : 0

  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.health_check[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.health_check[0].arn
}

resource "aws_sns_topic" "alerts" {
  count = local.monitoring_enabled ? 1 : 0

  name = "${var.project}-alerts"
}

resource "aws_sns_topic_subscription" "alert_email" {
  count = local.monitoring_enabled ? 1 : 0

  topic_arn = aws_sns_topic.alerts[0].arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# Each 5-minute period has about one check. Two failing periods in a row
# avoids alerting on a single blip, such as the few seconds of downtime
# during a deploy; missing data means the checks themselves stopped.
resource "aws_cloudwatch_metric_alarm" "site_down" {
  count = local.monitoring_enabled ? 1 : 0

  alarm_name        = "${var.project}-site-down"
  alarm_description = "${local.health_url} failed its health check twice in a row. Function logs: ${aws_cloudwatch_log_group.health_check[0].name}"

  namespace   = "AWS/Lambda"
  metric_name = "Errors"
  dimensions = {
    FunctionName = aws_lambda_function.health_check[0].function_name
  }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "breaching"

  alarm_actions = [aws_sns_topic.alerts[0].arn]
  ok_actions    = [aws_sns_topic.alerts[0].arn]
}
