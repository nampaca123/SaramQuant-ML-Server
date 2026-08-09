# SecureString 파라미터 복호화는 SSM 경유(ViaService)로만 허용해 키 사용 범위를 좁힌다
data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id

  ssm_calc_params_arn = "arn:aws:ssm:${var.region}:${local.account_id}:parameter/saramquant/calc/*"

  glue_arns = [
    "arn:aws:glue:${var.region}:${local.account_id}:catalog",
    "arn:aws:glue:${var.region}:${local.account_id}:database/${local.glue_db}",
    "arn:aws:glue:${var.region}:${local.account_id}:table/${local.glue_db}/*",
  ]

  lake_object_arns = [
    "arn:aws:s3:::${local.bucket}/warehouse/*",
    "arn:aws:s3:::${local.bucket}/staging/*",
    "arn:aws:s3:::${local.bucket}/athena-results/*",
    "arn:aws:s3:::${local.bucket}/run-summary/*",
  ]

  kms_decrypt_via_ssm = {
    Sid      = "DecryptSecureStringViaSsm"
    Effect   = "Allow"
    Action   = ["kms:Decrypt"]
    Resource = "*"
    Condition = {
      StringEquals = {
        "kms:ViaService" = "ssm.${var.region}.amazonaws.com"
      }
    }
  }
}

resource "aws_iam_role" "batch_task" {
  name = "${local.app_name}-batch-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "batch_task" {
  name = "${local.app_name}-batch-task"
  role = aws_iam_role.batch_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ListLakeBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = "arn:aws:s3:::${local.bucket}"
      },
      {
        Sid    = "ReadWriteLakeObjects"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:AbortMultipartUpload",
          "s3:ListMultipartUploadParts",
        ]
        Resource = local.lake_object_arns
      },
      {
        Sid    = "RunAthenaQueries"
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution",
          "athena:GetQueryExecution",
          "athena:StopQueryExecution",
          "athena:GetQueryResults",
        ]
        Resource = aws_athena_workgroup.saramquant.arn
      },
      {
        Sid    = "ManageGlueCatalog"
        Effect = "Allow"
        Action = [
          "glue:GetDatabase",
          "glue:GetDatabases",
          "glue:GetTable",
          "glue:GetTables",
          "glue:CreateTable",
          "glue:UpdateTable",
          "glue:DeleteTable",
          "glue:GetPartition",
          "glue:GetPartitions",
          "glue:BatchGetPartition",
          "glue:BatchCreatePartition",
          "glue:CreatePartition",
          "glue:UpdatePartition",
          "glue:DeletePartition",
          "glue:BatchDeletePartition",
        ]
        Resource = local.glue_arns
      },
      {
        Sid      = "WriteBatchLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.batch.arn}:*"
      },
      {
        Sid      = "ReadCalcParameters"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:GetParameters"]
        Resource = local.ssm_calc_params_arn
      },
      local.kms_decrypt_via_ssm,
    ]
  })
}

resource "aws_iam_role" "batch_exec" {
  name = "${local.app_name}-batch-exec"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "batch_exec_managed" {
  role       = aws_iam_role.batch_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "batch_exec" {
  name = "${local.app_name}-batch-exec"
  role = aws_iam_role.batch_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "InjectCalcParameters"
        Effect   = "Allow"
        Action   = ["ssm:GetParameters"]
        Resource = local.ssm_calc_params_arn
      },
      local.kms_decrypt_via_ssm,
    ]
  })
}

resource "aws_iam_role" "lambda_api" {
  name = "${local.app_name}-lambda-api"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_api_managed" {
  role       = aws_iam_role.lambda_api.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda_api" {
  name = "${local.app_name}-lambda-api"
  role = aws_iam_role.lambda_api.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ListLakeBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = "arn:aws:s3:::${local.bucket}"
      },
      {
        Sid      = "ReadWarehouseObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "arn:aws:s3:::${local.bucket}/warehouse/*"
      },
      {
        Sid      = "ReadGlueTable"
        Effect   = "Allow"
        Action   = ["glue:GetTable"]
        Resource = local.glue_arns
      },
      {
        Sid      = "ReadCalcParameters"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = local.ssm_calc_params_arn
      },
      local.kms_decrypt_via_ssm,
    ]
  })
}
