# Platform stack: Sentinel on AWS for the demo ("managed-lite", TDD §19.3). ECS Fargate services,
# Aurora Serverless v2 (PostgreSQL + pgvector), MSK Serverless (IAM auth), an ALB reached only through
# CloudFront (HTTPS, IP allow-list). Billed by the hour: apply for the demo, then `terraform destroy`.
# Cognito stays in the identity stack; the GitHub OIDC provider in the ci stack.
#
# Passwords never reach the Terraform state: they are ephemeral values written to Aurora and Secrets
# Manager through write-only arguments.

terraform {
  required_version = ">= 1.11"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "sentinel", Stack = "platform", ManagedBy = "terraform" }
  }
}

data "aws_caller_identity" "current" {}
data "aws_availability_zones" "available" { state = "available" }

data "terraform_remote_state" "identity" {
  backend = "local"
  config  = { path = "${path.module}/../identity/terraform.tfstate" }
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  azs        = slice(data.aws_availability_zones.available.names, 0, 2)
  cognito    = data.terraform_remote_state.identity.outputs.cognito
  namespace  = "sentinel.internal"
}
