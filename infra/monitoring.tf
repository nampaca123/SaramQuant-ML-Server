# 실행 실패 1건이면 즉시 SNS 알림 — 배치는 하루 몇 회라 임계값을 낮게 둔다
resource "aws_cloudwatch_metric_alarm" "pipeline_failed" {
  alarm_name          = "${local.app_name}-pipeline-failed"
  alarm_description   = "Step Functions pipeline execution failed."
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  dimensions          = { StateMachineArn = aws_sfn_state_machine.pipeline.arn }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
