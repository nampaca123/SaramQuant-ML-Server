# 컨테이너명 `pipeline`은 Step Functions가 Command 오버라이드 대상으로 참조하므로 고정이다
resource "aws_ecs_cluster" "calc" {
  name = local.app_name

  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "calc" {
  cluster_name       = aws_ecs_cluster.calc.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]
}

resource "aws_ecs_task_definition" "pipeline" {
  family                   = "${local.app_name}-pipeline"
  cpu                      = "4096"
  memory                   = "8192"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  task_role_arn            = aws_iam_role.batch_task.arn
  execution_role_arn       = aws_iam_role.batch_exec.arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([{
    name      = "pipeline"
    image     = "${aws_ecr_repository.batch.repository_url}:${var.batch_image_tag}"
    essential = true

    environment = [
      { name = "LAKE_BUCKET", value = local.bucket },
      { name = "GLUE_DATABASE", value = local.glue_db },
      { name = "ATHENA_WORKGROUP", value = local.athena_workgroup },
      { name = "RUN_SUMMARY_PREFIX", value = "run-summary/" },
      { name = "AWS_REGION_NAME", value = var.region },
    ]

    secrets = [
      { name = "ALPACA_API_KEY", valueFrom = aws_ssm_parameter.alpaca_api_key.arn },
      { name = "ALPACA_SECRET_KEY", valueFrom = aws_ssm_parameter.alpaca_secret_key.arn },
      { name = "DART_API_KEY", valueFrom = aws_ssm_parameter.dart_api_key.arn },
      { name = "ECOS_API_KEY", valueFrom = aws_ssm_parameter.ecos_api_key.arn },
      { name = "FRED_API_KEY", valueFrom = aws_ssm_parameter.fred_api_key.arn },
      { name = "FINNHUB_API_KEY", valueFrom = aws_ssm_parameter.finnhub_api_key.arn },
      { name = "KRX_ID", valueFrom = aws_ssm_parameter.krx_id.arn },
      { name = "KRX_PASSWORD", valueFrom = aws_ssm_parameter.krx_password.arn },
      { name = "CALC_AUTH_KEY", valueFrom = aws_ssm_parameter.calc_auth_key.arn },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.batch.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "pipeline"
      }
    }
  }])
}
