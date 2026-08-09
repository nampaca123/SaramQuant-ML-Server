# 시크릿 값은 for_each 키를 오염시키므로 파라미터마다 개별 리소스로 선언한다
resource "aws_ssm_parameter" "alpaca_api_key" {
  name  = "/saramquant/calc/alpaca_api_key"
  type  = "SecureString"
  value = var.alpaca_api_key
}

resource "aws_ssm_parameter" "alpaca_secret_key" {
  name  = "/saramquant/calc/alpaca_secret_key"
  type  = "SecureString"
  value = var.alpaca_secret_key
}

resource "aws_ssm_parameter" "dart_api_key" {
  name  = "/saramquant/calc/dart_api_key"
  type  = "SecureString"
  value = var.dart_api_key
}

resource "aws_ssm_parameter" "ecos_api_key" {
  name  = "/saramquant/calc/ecos_api_key"
  type  = "SecureString"
  value = var.ecos_api_key
}

resource "aws_ssm_parameter" "fred_api_key" {
  name  = "/saramquant/calc/fred_api_key"
  type  = "SecureString"
  value = var.fred_api_key
}

resource "aws_ssm_parameter" "finnhub_api_key" {
  name  = "/saramquant/calc/finnhub_api_key"
  type  = "SecureString"
  value = var.finnhub_api_key
}

resource "aws_ssm_parameter" "krx_id" {
  name  = "/saramquant/calc/krx_id"
  type  = "SecureString"
  value = var.krx_id
}

resource "aws_ssm_parameter" "krx_password" {
  name  = "/saramquant/calc/krx_password"
  type  = "SecureString"
  value = var.krx_password
}

resource "aws_ssm_parameter" "calc_auth_key" {
  name  = "/saramquant/calc/calc_auth_key"
  type  = "SecureString"
  value = var.calc_auth_key
}
