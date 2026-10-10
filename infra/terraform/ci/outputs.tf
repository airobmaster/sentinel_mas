output "eval_gate_role_arn" {
  description = "Set as the GitHub repository variable AWS_ROLE_ARN"
  value       = aws_iam_role.eval_gate.arn
}
