# Identity stack: the Cognito user pool for the Sentinel API and workbench.
# Long-lived and free at demo scale, so it is kept apart from the hourly-billed platform stack
# (Aurora, MSK, ECS), which is created for the demo and destroyed afterwards.
#
# Users are not managed here (their passwords would land in the Terraform state): create them with
# `sentinel auth bootstrap`, which sets passwords directly and writes them only to .env.

terraform {
  required_version = ">= 1.9"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "sentinel", Stack = "identity", ManagedBy = "terraform" }
  }
}

resource "aws_cognito_user_pool" "sentinel" {
  name                = var.name
  deletion_protection = "INACTIVE" # demo pool: `terraform destroy` must work

  username_configuration {
    case_sensitive = false
  }

  # Users are created by an administrator only; no self sign-up, no emails sent (no forgot-password flow)
  admin_create_user_config {
    allow_admin_create_user_only = true
  }
  account_recovery_setting {
    recovery_mechanism {
      name     = "admin_only"
      priority = 1
    }
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 3
  }
}

# Roles (TDD §12; Functional Spec BR-08/09/10, UC-03, UC-05). The API reads them from the
# `cognito:groups` claim of the access token.
resource "aws_cognito_user_group" "role" {
  for_each     = var.roles
  name         = each.key
  description  = each.value
  user_pool_id = aws_cognito_user_pool.sentinel.id
}

# Hosted sign-in page at https://<prefix>.auth.<region>.amazoncognito.com (used by the workbench)
resource "aws_cognito_user_pool_domain" "sentinel" {
  domain       = var.domain_prefix
  user_pool_id = aws_cognito_user_pool.sentinel.id
}

# React workbench: browser sign-in (authorization code + PKCE), no client secret
resource "aws_cognito_user_pool_client" "workbench" {
  name                                 = "${var.name}-workbench"
  user_pool_id                         = aws_cognito_user_pool.sentinel.id
  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = var.workbench_callback_urls
  logout_urls                          = var.workbench_logout_urls
  explicit_auth_flows                  = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors        = "ENABLED"
  access_token_validity                = 60
  id_token_validity                    = 60
  refresh_token_validity               = 1
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

# Test console, CLI and integration tests: username + password sign-in, no client secret
resource "aws_cognito_user_pool_client" "tools" {
  name                          = "${var.name}-tools"
  user_pool_id                  = aws_cognito_user_pool.sentinel.id
  generate_secret               = false
  explicit_auth_flows           = ["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors = "ENABLED"
  access_token_validity         = 60
  id_token_validity             = 60
  refresh_token_validity        = 1
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}
