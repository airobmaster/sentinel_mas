# Aurora Serverless v2 (PostgreSQL 16 + pgvector) and MSK Serverless (IAM auth), private subnets only.

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

resource "aws_rds_cluster" "main" {
  cluster_identifier         = var.name
  engine                     = "aurora-postgresql"
  engine_mode                = "provisioned"
  engine_version             = "16.15"
  database_name              = "sentinel"
  master_username            = "sentinel"
  master_password_wo         = ephemeral.random_password.db.result
  master_password_wo_version = var.secrets_version
  db_subnet_group_name       = aws_db_subnet_group.main.name
  vpc_security_group_ids     = [aws_security_group.data.id]
  storage_encrypted          = true
  skip_final_snapshot        = true # demo stack, destroyed after the demo
  deletion_protection        = false
  backup_retention_period    = 1
  serverlessv2_scaling_configuration {
    min_capacity = 0.5
    max_capacity = 4
  }
}

resource "aws_rds_cluster_instance" "main" {
  identifier         = "${var.name}-1"
  cluster_identifier = aws_rds_cluster.main.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.main.engine
  engine_version     = aws_rds_cluster.main.engine_version
}

resource "aws_msk_serverless_cluster" "main" {
  cluster_name = var.name
  vpc_config {
    subnet_ids         = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.data.id]
  }
  client_authentication {
    sasl {
      iam { enabled = true }
    }
  }
}

# One secret, injected into the containers by key. Written with write-only arguments: not in the state.
resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.name}/app"
  recovery_window_in_days = 0 # demo: delete at once on destroy
}

locals {
  db_host = aws_rds_cluster.main.endpoint
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
