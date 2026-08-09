# state 락은 DynamoDB 대신 S3 lockfile 사용
terraform {
  backend "s3" {
    bucket       = "saramquant-tfstate"
    key          = "calc-server/terraform.tfstate"
    region       = "ap-northeast-2"
    encrypt      = true
    use_lockfile = true
  }
}
