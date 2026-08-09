# 라우트 키의 맵 키는 aws_lambda_function.api의 키와 같아야 통합/권한이 올바른 함수를 가리킨다
locals {
  api_routes = {
    "analysis"             = "POST /internal/portfolios/full-analysis"
    "portfolio-simulation" = "POST /internal/portfolios/simulation"
    "stock-simulation"     = "GET /internal/stocks/{symbol}/simulation"
    "price-lookup"         = "POST /internal/portfolios/price-lookup"
    "health"               = "GET /health"
  }
}

resource "aws_apigatewayv2_api" "calc" {
  name          = "${local.app_name}-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.calc.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_apigatewayv2_integration" "api" {
  for_each = local.api_routes

  api_id                 = aws_apigatewayv2_api.calc.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api[each.key].invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "api" {
  for_each = local.api_routes

  api_id    = aws_apigatewayv2_api.calc.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.api[each.key].id}"
}

resource "aws_lambda_permission" "api" {
  for_each = local.api_routes

  statement_id  = "AllowInvokeFromHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api[each.key].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.calc.execution_arn}/*/*"
}

output "api_endpoint" {
  description = "Base URL of the calc HTTP API ($default stage)."
  value       = aws_apigatewayv2_api.calc.api_endpoint
}
