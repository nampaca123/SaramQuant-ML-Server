# 쿼리당 10GB 스캔 상한으로 Athena 비용 폭주를 차단한다
resource "aws_glue_catalog_database" "saramquant" {
  name = local.glue_db
}

resource "aws_athena_workgroup" "saramquant" {
  name = local.athena_workgroup

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = false
    bytes_scanned_cutoff_per_query     = 10737418240

    result_configuration {
      output_location = "s3://${local.bucket}/athena-results/"
    }
  }
}
