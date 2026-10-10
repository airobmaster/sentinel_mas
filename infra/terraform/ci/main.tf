# CI stack: lets GitHub Actions call Bedrock for the eval gate, with no stored AWS keys.
# GitHub's OIDC token is exchanged for a short-lived session of this role. The role can only
# invoke models, and only workflows from this repository can assume it.

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
    tags = { Project = "sentinel", Stack = "ci", ManagedBy = "terraform" }
  }
}

data "aws_caller_identity" "current" {}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      # GitHub's subject carries the owner and repository IDs (owner@id/repo@id), which also pins
      # the exact repository if one with the same name is ever recreated; the plain form is kept too.
      values = ["repo:${var.github_repo}:*", "repo:${var.github_repo_with_ids}:*"]
    }
  }
}

resource "aws_iam_role" "eval_gate" {
  name                 = "${var.name}-eval-gate"
  description          = "GitHub Actions eval gate: Bedrock model calls only"
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
}

# Cross-region inference profiles (eu.*) route to foundation models in other EU regions,
# so the model ARNs are allowed in any region; the profiles only in this account.
data "aws_iam_policy_document" "bedrock" {
  statement {
    actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = [
      "arn:aws:bedrock:*::foundation-model/*",
      "arn:aws:bedrock:*:${data.aws_caller_identity.current.account_id}:inference-profile/*",
    ]
  }
}

resource "aws_iam_role_policy" "bedrock" {
  name   = "bedrock-invoke"
  role   = aws_iam_role.eval_gate.id
  policy = data.aws_iam_policy_document.bedrock.json
}
