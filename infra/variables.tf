# 기본값 없음 + validation — 값 누락 시 CI plan 단계에서 즉시 실패시킨다

variable "region" {
  description = "AWS region for all calc-server resources."
  type        = string

  validation {
    condition     = length(var.region) > 0
    error_message = "region must be non-empty. Set GitHub variable AWS_REGION_APNE2."
  }
}

variable "alpaca_api_key" {
  description = "Alpaca market data API key."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.alpaca_api_key) > 0
    error_message = "alpaca_api_key must be non-empty. Set GitHub secret ALPACA_API_KEY."
  }
}

variable "alpaca_secret_key" {
  description = "Alpaca market data API secret key."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.alpaca_secret_key) > 0
    error_message = "alpaca_secret_key must be non-empty. Set GitHub secret ALPACA_SECRET_KEY."
  }
}

variable "dart_api_key" {
  description = "DART open API key."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.dart_api_key) > 0
    error_message = "dart_api_key must be non-empty. Set GitHub secret DART_API_KEY."
  }
}

variable "ecos_api_key" {
  description = "ECOS (Bank of Korea) API key."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.ecos_api_key) > 0
    error_message = "ecos_api_key must be non-empty. Set GitHub secret ECOS_API_KEY."
  }
}

variable "fred_api_key" {
  description = "FRED API key."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.fred_api_key) > 0
    error_message = "fred_api_key must be non-empty. Set GitHub secret FRED_API_KEY."
  }
}

variable "finnhub_api_key" {
  description = "Finnhub API key."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.finnhub_api_key) > 0
    error_message = "finnhub_api_key must be non-empty. Set GitHub secret FINNHUB_API_KEY."
  }
}

variable "krx_id" {
  description = "KRX portal login ID."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.krx_id) > 0
    error_message = "krx_id must be non-empty. Set GitHub secret KRX_ID."
  }
}

variable "krx_password" {
  description = "KRX portal login password."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.krx_password) > 0
    error_message = "krx_password must be non-empty. Set GitHub secret KRX_PASSWORD."
  }
}

variable "calc_auth_key" {
  description = "Shared x-api-key that the gateway sends to the calc API."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.calc_auth_key) > 0
    error_message = "calc_auth_key must be non-empty. Set GitHub secret CALC_AUTH_KEY."
  }
}

variable "batch_image_tag" {
  description = "ECR image tag for the batch (Fargate) image."
  type        = string

  validation {
    condition     = length(var.batch_image_tag) > 0
    error_message = "batch_image_tag must be non-empty. Set GitHub Actions env TF_VAR_batch_image_tag in deploy.yml (tag = Dockerfile+source hash)."
  }
}

variable "api_image_tag" {
  description = "ECR image tag for the API (Lambda) image."
  type        = string

  validation {
    condition     = length(var.api_image_tag) > 0
    error_message = "api_image_tag must be non-empty. Set GitHub Actions env TF_VAR_api_image_tag in deploy.yml (tag = Dockerfile+source hash)."
  }
}
