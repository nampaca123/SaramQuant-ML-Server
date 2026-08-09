# 버킷은 이미 계정에 존재하므로 CI plan/apply 시 import 블록으로 흡수한다
import {
  to = aws_s3_bucket.lake
  id = "saramquant-bucket"
}

resource "aws_s3_bucket" "lake" {
  bucket = local.bucket
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket = aws_s3_bucket.lake.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id

  rule {
    id     = "expire-staging"
    status = "Enabled"

    filter {
      prefix = "staging/"
    }

    expiration {
      days = 7
    }
  }

  rule {
    id     = "expire-athena-results"
    status = "Enabled"

    filter {
      prefix = "athena-results/"
    }

    expiration {
      days = 14
    }
  }

  rule {
    id     = "abort-incomplete-multipart-upload"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}
