locals {
  ecr_keep_last_3 = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep only the 3 most recent images."
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 3
      }
      action = {
        type = "expire"
      }
    }]
  })
}

resource "aws_ecr_repository" "batch" {
  name = "${local.app_name}-batch"
}

resource "aws_ecr_repository" "api" {
  name = "${local.app_name}-api"
}

resource "aws_ecr_lifecycle_policy" "batch" {
  repository = aws_ecr_repository.batch.name
  policy     = local.ecr_keep_last_3
}

resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name
  policy     = local.ecr_keep_last_3
}
