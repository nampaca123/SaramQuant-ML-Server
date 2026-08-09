# 상태머신 이름은 iam_sfn.tf의 local.state_machine_arn과 반드시 일치해야 한다
resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/${local.app_name}/sfn"
  retention_in_days = 30
}

resource "aws_sfn_state_machine" "pipeline" {
  name     = "${local.app_name}-pipeline"
  role_arn = aws_iam_role.sfn.arn
  type     = "STANDARD"

  definition = templatefile("${path.module}/sfn/pipeline.asl.json", {
    cluster_arn  = aws_ecs_cluster.calc.arn
    task_def_arn = aws_ecs_task_definition.pipeline.arn
    subnets      = jsonencode(data.aws_subnets.default.ids)
    sg           = aws_security_group.batch.id
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  depends_on = [aws_iam_role_policy.sfn]
}
