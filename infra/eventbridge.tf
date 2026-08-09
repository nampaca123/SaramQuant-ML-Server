# 10개 규칙 모두 ENABLED — 콜드 ETL 완주로 DISABLED 가드 해제
# fs 크론은 분기 보고서 마감 다음 날 KST 03:00을 전일 UTC 18:00으로 환산한 값이다
locals {
  fs_crons = {
    q1 = "cron(0 18 6 4 ? *)"
    q2 = "cron(0 18 21 5 ? *)"
    q3 = "cron(0 18 20 8 ? *)"
    q4 = "cron(0 18 20 11 ? *)"
  }

  pipeline_schedules = merge(
    {
      "daily-kr" = { cron = "cron(0 9 ? * MON-FRI *)", command = "kr" }
      "daily-us" = { cron = "cron(0 0 ? * TUE-SAT *)", command = "us" }
    },
    { for q, cron in local.fs_crons : "kr-fs-${q}" => { cron = cron, command = "kr-fs" } },
    { for q, cron in local.fs_crons : "us-fs-${q}" => { cron = cron, command = "us-fs" } },
  )
}

resource "aws_cloudwatch_event_rule" "pipeline" {
  for_each = local.pipeline_schedules

  name                = "${local.app_name}-${each.key}"
  description         = "Runs the ${each.value.command} pipeline via Step Functions."
  schedule_expression = each.value.cron
  state               = "ENABLED"
}

resource "aws_cloudwatch_event_target" "pipeline" {
  for_each = local.pipeline_schedules

  rule      = aws_cloudwatch_event_rule.pipeline[each.key].name
  target_id = "state-machine"
  arn       = aws_sfn_state_machine.pipeline.arn
  role_arn  = aws_iam_role.events.arn
  input     = jsonencode({ command = each.value.command })
}
