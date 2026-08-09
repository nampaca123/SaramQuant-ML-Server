# 상태머신은 Task 14에서 만들어지므로 ARN을 계정/리전으로 직접 조립해 순환 참조를 피한다
locals {
  state_machine_arn = "arn:aws:states:${var.region}:${local.account_id}:stateMachine:${local.app_name}-pipeline"
}

resource "aws_iam_role" "sfn" {
  name = "${local.app_name}-sfn"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "sfn" {
  name = "${local.app_name}-sfn"
  role = aws_iam_role.sfn.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RunPipelineTask"
        Effect = "Allow"
        Action = ["ecs:RunTask"]
        Resource = [
          aws_ecs_task_definition.pipeline.arn_without_revision,
          "${aws_ecs_task_definition.pipeline.arn_without_revision}:*",
        ]
      },
      {
        Sid      = "ControlPipelineTask"
        Effect   = "Allow"
        Action   = ["ecs:StopTask", "ecs:DescribeTasks"]
        Resource = "arn:aws:ecs:${var.region}:${local.account_id}:task/${aws_ecs_cluster.calc.name}/*"
      },
      {
        Sid      = "PassTaskRoles"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.batch_task.arn, aws_iam_role.batch_exec.arn]
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "ecs-tasks.amazonaws.com"
          }
        }
      },
      {
        Sid      = "ManageSyncExecutionRule"
        Effect   = "Allow"
        Action   = ["events:PutTargets", "events:PutRule", "events:DescribeRule"]
        Resource = "arn:aws:events:${var.region}:${local.account_id}:rule/StepFunctionsGetEventsForECSTaskRule"
      },
      {
        Sid    = "DeliverExecutionLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups",
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_iam_role" "events" {
  name = "${local.app_name}-events"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "events" {
  name = "${local.app_name}-events"
  role = aws_iam_role.events.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "StartPipelineExecution"
      Effect   = "Allow"
      Action   = ["states:StartExecution"]
      Resource = local.state_machine_arn
    }]
  })
}
