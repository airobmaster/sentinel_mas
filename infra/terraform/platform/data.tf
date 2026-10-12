# Database and secrets, private subnets only.

ephemeral "random_password" "db" {
  length  = 32
  special = false # goes into connection strings
}

ephemeral "random_password" "keys" {
  count   = 3 # API dev tokens, MCP service tokens, PII token HMAC key
  length  = 48
  special = false
}

resource "aws_db_subnet_group" "main" {
  name       = var.name
  subnet_ids = aws_subnet.private[*].id
}

# RDS PostgreSQL 16 on the free-tier instance class (the account is on the AWS Free plan, which blocks
# Aurora Serverless and MSK). pgvector is available as an extension. Kafka runs on ECS (ecs.tf).
resource "aws_db_instance" "main" {
  identifier              = var.name
  engine                  = "postgres"
  engine_version          = "16"
  instance_class          = "db.t4g.micro"
  allocated_storage       = 20
  storage_type            = "gp3"
  db_name                 = "sentinel"
  username                = "sentinel"
  password_wo             = ephemeral.random_password.db.result
  password_wo_version     = var.secrets_version
  db_subnet_group_name    = aws_db_subnet_group.main.name
  vpc_security_group_ids  = [aws_security_group.data.id]
  publicly_accessible     = false
  multi_az                = false
  storage_encrypted       = true
  skip_final_snapshot     = true # demo stack, destroyed after the demo
  deletion_protection     = false
  backup_retention_period = 0
  apply_immediately       = true
}

# One secret, injected into the containers by key. Written with write-only arguments: not in the state.
resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.name}/app"
  recovery_window_in_days = 0 # demo: delete at once on destroy
}

locals {
  db_host = aws_db_instance.main.address
}

# Cognito test-user passwords for the workbench role switch (demo only), kept apart so they can change
# without regenerating the database password
resource "aws_secretsmanager_secret" "demo_users" {
  name                    = "${var.name}/demo-users"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "demo_users" {
  secret_id                = aws_secretsmanager_secret.demo_users.id
  secret_string_wo         = var.cognito_test_users == null ? "{}" : var.cognito_test_users
  secret_string_wo_version = var.demo_users_version
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string_wo = jsonencode({
    pg_dsn         = "postgresql://sentinel:${ephemeral.random_password.db.result}@${local.db_host}:5432/sentinel?sslmode=require"
    airflow_dsn    = "postgresql+psycopg2://sentinel:${ephemeral.random_password.db.result}@${local.db_host}:5432/airflow?sslmode=require"
    pg_password    = ephemeral.random_password.db.result
    api_dev_secret = ephemeral.random_password.keys[0].result
    mcp_dev_secret = ephemeral.random_password.keys[1].result
    pii_token_key  = ephemeral.random_password.keys[2].result
    neo4j_password = var.neo4j_password == null ? "" : var.neo4j_password
  })
  secret_string_wo_version = var.secrets_version
}
