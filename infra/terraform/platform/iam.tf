# ECR repositories, the ECS execution role, task roles (least privilege per workload) and the GitHub
# deploy role (push images, roll services).

resource "aws_ecr_repository" "repo" {
  for_each             = toset(["sentinel", "mcp", "workbench", "airflow", "ops"])
  name                 = "${var.name}/${each.key}"
  image_tag_mutability = "MUTABLE"
  force_delete         = true # demo: destroy removes the images too
}

resource "aws_ecr_lifecycle_policy" "repo" {
  for_each   = aws_ecr_repository.repo
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1, description = "Keep the last 10 images",
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 },
      action       = { type = "expire" }
    }]
  })
}

data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Execution role: pull images, write logs, read the app secret
resource "aws_iam_role" "execution" {
  name               = "${var.name}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secret" {
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "secretsmanager:GetSecretValue", Resource = aws_secretsmanager_secret.app.arn }]
  })
}

# Task roles: "agents" (Bedrock: workers, Airflow, one-off tasks, policy KB embeddings),
# "api" and "plain" (no AWS permissions: API, MCP data servers, workbench, Kafka, Presidio, observability)
locals {
  bedrock_statement = {
    Effect   = "Allow"
    Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    Resource = ["arn:aws:bedrock:*::foundation-model/*", "arn:aws:bedrock:*:${local.account_id}:inference-profile/*"]
  }
  task_policies = {
    agents = [local.bedrock_statement]
    api    = []
    plain  = []
  }
}

resource "aws_iam_role" "task" {
  for_each           = local.task_policies
  name               = "${var.name}-task-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy" "task" {
  for_each = { for k, v in local.task_policies : k => v if length(v) > 0 }
  role     = aws_iam_role.task[each.key].id
  policy   = jsonencode({ Version = "2012-10-17", Statement = each.value })
}

# GitHub Actions deploy role (deploy.yml): push images to these repositories and roll the ECS services
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_role" "deploy" {
  name = "${var.name}-deploy"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn }
      Condition = {
        StringEquals = { "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com" }
        StringLike   = { "token.actions.githubusercontent.com:sub" = "repo:${var.github_repo_with_ids}:*" }
      }
    }]
  })
}

resource "aws_iam_role_policy" "deploy" {
  role = aws_iam_role.deploy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken", "ecs:ListServices"], Resource = "*" },
      {
        Effect = "Allow"
        Action = ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
        Resource = [for r in aws_ecr_repository.repo : r.arn]
      },
      {
        Effect   = "Allow", Action = ["ecs:UpdateService", "ecs:DescribeServices"]
        Resource = "arn:aws:ecs:${var.region}:${local.account_id}:service/${var.name}/*"
      },
    ]
  })
}
