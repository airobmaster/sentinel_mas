output "urls" {
  value = local.urls
}

output "workbench_callback_url" {
  description = "Add to the identity stack's workbench_callback_urls (and the origin to workbench_logout_urls)"
  value       = "${local.urls.workbench}/callback"
}

output "ecr" {
  value = { for k, r in aws_ecr_repository.repo : k => r.repository_url }
}

output "deploy_role_arn" {
  description = "GitHub repository variable AWS_DEPLOY_ROLE_ARN (deploy.yml)"
  value       = aws_iam_role.deploy.arn
}

# For infra/aws/*.sh (one-off tasks)
output "run_task" {
  value = {
    cluster         = aws_ecs_cluster.main.name
    task_definition = aws_ecs_task_definition.task.family
    subnets         = join(",", aws_subnet.private[*].id)
    security_group  = aws_security_group.tasks.id
    log_group       = aws_cloudwatch_log_group.main.name
  }
}
