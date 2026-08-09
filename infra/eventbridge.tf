# 10개 규칙 모두 ENABLED — 콜드 ETL 완주로 DISABLED 가드 해제
# fs 크론은 마감 다음 날 KST 03:00(kr) / 04:00(us)의 전일 UTC 환산 — us-fs를 1시간 늦춰 공유 staging 동시 접근을 피한다
locals {
  kr_fs_crons = {
    q1 = "cron(0 18 6 4 ? *)"
    q2 = "cron(0 18 21 5 ? *)"
    q3 = "cron(0 18 20 8 ? *)"
    q4 = "cron(0 18 20 11 ? *)"
  }

  us_fs_crons = {
    q1 = "cron(0 19 6 4 ? *)"
    q2 = "cron(0 19 21 5 ? *)"
    q3 = "cron(0 19 20 8 ? *)"
    q4 = "cron(0 19 20 11 ? *)"
  }

  pipeline_schedules = merge(
    {
      "daily-kr" = { cron = "cron(0 9 ? * MON-FRI *)", command = "kr" }
      "daily-us" = { cron = "cron(0 0 ? * TUE-SAT *)", command = "us" }
    },
    { for q, cron in local.kr_fs_crons : "kr-fs-${q}" => { cron = cron, command = "kr-fs" } },
    { for q, cron in local.us_fs_crons : "us-fs-${q}" => { cron = cron, command = "us-fs" } },
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
