# Lambda 로그 그룹을 미리 만들어 보존기간 30일을 강제한다(함수는 후속 태스크에서 동일 이름으로 생성)
resource "aws_cloudwatch_log_group" "batch" {
  name              = "/${local.app_name}/batch"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "lambda" {
  for_each = toset([
    "analysis",
    "portfolio-simulation",
    "stock-simulation",
    "price-lookup",
    "health",
  ])

  name              = "/aws/lambda/${local.app_name}-${each.value}"
  retention_in_days = 30
}

resource "aws_sns_topic" "alerts" {
  name = "${local.app_name}-alerts"
}

resource "aws_sns_topic_subscription" "alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = "nampaca123@gmail.com"
}
