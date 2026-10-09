# Copy into .env (none of these are secrets): see `.env.example`
output "env" {
  value = <<-EOT
    SENTINEL_AUTH_MODE=cognito
    SENTINEL_COGNITO_USER_POOL_ID=${aws_cognito_user_pool.sentinel.id}
    SENTINEL_COGNITO_TOOLS_CLIENT_ID=${aws_cognito_user_pool_client.tools.id}
    SENTINEL_COGNITO_WORKBENCH_CLIENT_ID=${aws_cognito_user_pool_client.workbench.id}
    SENTINEL_COGNITO_DOMAIN=https://${aws_cognito_user_pool_domain.sentinel.domain}.auth.${var.region}.amazoncognito.com
  EOT
}

output "issuer" {
  value = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.sentinel.id}"
}
