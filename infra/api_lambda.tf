# 함수 5개가 단일 이미지를 공유하고 image_config.command로만 갈린다 — 함수명은 기존 로그 그룹 이름과 일치해야 한다
locals {
  api_functions = {
    "analysis" = {
      handler = "handle_analysis"
      memory  = 2048
      timeout = 30
    }
    "portfolio-simulation" = {
      handler = "handle_portfolio_simulation"
      memory  = 2048
      timeout = 30
    }
    "stock-simulation" = {
      handler = "handle_stock_simulation"
      memory  = 2048
      timeout = 30
    }
    "price-lookup" = {
      handler = "handle_price_lookup"
      memory  = 2048
      timeout = 30
    }
    # 256MB/5s에서는 컨테이너 init이 10초 캡을 넘겨 500이 났다(CPU가 메모리에 비례) — 1024MB/10s로 올린다
    "health" = {
      handler = "handle_health"
      memory  = 1024
      timeout = 10
    }
  }

  api_lambda_env = {
    LAKE_BUCKET          = local.bucket
    GLUE_DATABASE        = local.glue_db
    ATHENA_WORKGROUP     = local.athena_workgroup
    AWS_REGION_NAME      = var.region
    DUCKDB_MEMORY_LIMIT  = "1GB"
    DUCKDB_EXTENSION_DIR = "/opt/duckdb-extensions"
    HOME                 = "/tmp"
    CALC_AUTH_KEY        = var.calc_auth_key
    ALPACA_API_KEY       = var.alpaca_api_key
    ALPACA_SECRET_KEY    = var.alpaca_secret_key
    ECOS_API_KEY         = var.ecos_api_key
    FINNHUB_API_KEY      = var.finnhub_api_key
  }
}

resource "aws_lambda_function" "api" {
  for_each = local.api_functions

  function_name = "${local.app_name}-${each.key}"
  role          = aws_iam_role.lambda_api.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.api.repository_url}:${var.api_image_tag}"
  memory_size   = each.value.memory
  timeout       = each.value.timeout

  image_config {
    command = ["app.api.lambda_handlers.${each.value.handler}"]
  }

  ephemeral_storage {
    size = 1024
  }

  environment {
    variables = local.api_lambda_env
  }

  depends_on = [
    aws_cloudwatch_log_group.lambda,
    aws_iam_role_policy.lambda_api,
    aws_iam_role_policy_attachment.lambda_api_managed,
  ]
}
