# ECS Fargate: one service per workload, found by name through Cloud Map (<service>.sentinel.internal).
# Mirrors docker-compose.yml: api, worker (+ OPA sidecar), six MCP servers, Presidio, workbench, Airflow,
# observability (collector + Tempo + Prometheus + Grafana in one task; traces are ephemeral).

resource "aws_ecs_cluster" "main" {
  name = var.name
}

resource "aws_cloudwatch_log_group" "main" {
  name              = "/${var.name}"
  retention_in_days = 3
}

resource "aws_service_discovery_private_dns_namespace" "main" {
  name = local.namespace
  vpc  = aws_vpc.main.id
}

locals {
  img = merge(
    { for r in ["sentinel", "mcp", "workbench", "airflow"] : r => "${aws_ecr_repository.repo[r].repository_url}:${var.image_tag}" },
    { for t in ["opa", "otel", "tempo", "prometheus", "grafana"] : t => "${aws_ecr_repository.repo["ops"].repository_url}:${t}" },
  )
  secret = aws_secretsmanager_secret.app.arn
  urls = {
    workbench = "https://${aws_cloudfront_distribution.app["workbench"].domain_name}"
    grafana   = "https://${aws_cloudfront_distribution.app["grafana"].domain_name}"
    airflow   = "https://${aws_cloudfront_distribution.app["airflow"].domain_name}"
  }
  # AuraDB Free names the database after the instance (the first label of the address), not "neo4j"
  neo4j_database = split(".", split("://", var.neo4j_uri)[1])[0]
  mcp_servers    = ["case_mgmt", "kyc_profile", "txn_history", "screening", "graph_query", "policy_kb"]

  # Settings shared by the Sentinel processes (API, workers, Airflow tasks, one-off tasks)
  sentinel_env = {
    AWS_REGION                           = var.region
    SENTINEL_AWS_REGION                  = var.region
    SENTINEL_ENVIRONMENT                 = "aws"
    SENTINEL_DATA_BACKEND                = "postgres"
    SENTINEL_PG_POOL_SIZE                = "3"
    SENTINEL_TOOL_MODE                   = "mcp"
    SENTINEL_MCP_URLS                    = jsonencode({ for s in local.mcp_servers : s => "http://mcp-${replace(s, "_", "-")}.${local.namespace}:8000/mcp/" })
    SENTINEL_KAFKA_BOOTSTRAP             = "kafka.${local.namespace}:9092"
    SENTINEL_KAFKA_PARTITIONS            = "3"
    SENTINEL_OTEL_ENDPOINT               = "http://observability.${local.namespace}:4318"
    SENTINEL_PRESIDIO_URL                = "http://presidio.${local.namespace}:3000"
    SENTINEL_NEO4J_URI                   = var.neo4j_uri
    SENTINEL_NEO4J_USER                  = var.neo4j_user
    SENTINEL_NEO4J_DATABASE              = local.neo4j_database
    SENTINEL_AUTH_MODE                   = "cognito"
    SENTINEL_COGNITO_USER_POOL_ID        = local.cognito.user_pool_id
    SENTINEL_COGNITO_TOOLS_CLIENT_ID     = local.cognito.tools_client_id
    SENTINEL_COGNITO_WORKBENCH_CLIENT_ID = local.cognito.workbench_client_id
    SENTINEL_COGNITO_DOMAIN              = local.cognito.domain
    SENTINEL_GRAFANA_URL                 = local.urls.grafana
    SENTINEL_BLIND_MODE_PERCENT          = "20"
    SENTINEL_DEMO_ROLE_SWITCH            = tostring(var.demo_role_switch) # demo only: behind the IP allow-list
    LANGSMITH_TRACING                    = "false"                        # BR-14: no LangSmith outside development
  }
  sentinel_secrets = {
    SENTINEL_PG_DSN         = "pg_dsn"
    SENTINEL_API_DEV_SECRET = "api_dev_secret"
    SENTINEL_MCP_DEV_SECRET = "mcp_dev_secret"
    SENTINEL_PII_TOKEN_KEY  = "pii_token_key"
    SENTINEL_NEO4J_PASSWORD = "neo4j_password"
  }
  mcp_env = {
    AWS_REGION              = var.region
    SENTINEL_AWS_REGION     = var.region
    SENTINEL_DATA_BACKEND   = "postgres"
    SENTINEL_PG_POOL_SIZE   = "3"
    SENTINEL_NEO4J_URI      = var.neo4j_uri
    SENTINEL_NEO4J_USER     = var.neo4j_user
    SENTINEL_NEO4J_DATABASE = local.neo4j_database
  }
  mcp_secrets = {
    SENTINEL_PG_DSN         = "pg_dsn"
    SENTINEL_MCP_DEV_SECRET = "mcp_dev_secret"
    SENTINEL_NEO4J_PASSWORD = "neo4j_password"
  }

  # name => { cpu, memory, role, lb (target group key or null), containers }
  # container: { name, image, command?, entryPoint?, port?, env, secrets }
  services = merge(
    {
      api = {
        cpu = 512, memory = 1024, role = "api", lb = null
        containers = [{ name = "api", image = local.img.sentinel, command = ["api", "--host", "0.0.0.0", "--port", "8000"],
          port = 8000, env = local.sentinel_env, secrets = local.sentinel_secrets,
        whole_secrets = var.demo_role_switch ? { SENTINEL_COGNITO_TEST_USERS = aws_secretsmanager_secret.demo_users.arn } : {} }]
      }
      worker = {
        cpu = 1024, memory = 2048, role = "agents", lb = null
        containers = [
          { name = "worker", image = local.img.sentinel, command = ["worker", "all"], port = 9464,
          env = merge(local.sentinel_env, { SENTINEL_OPA_URL = "http://localhost:8181" }), secrets = local.sentinel_secrets },
          { name = "opa", image = local.img.opa, port = 8181, env = {}, secrets = {} },
        ]
      }
      # Single-node Kafka (KRaft), as in docker-compose.yml. Topics are created by bootstrap.sh and
      # recreated automatically if the task restarts (messages are not kept across restarts).
      kafka = {
        cpu = 512, memory = 1024, role = "plain", lb = null
        containers = [{
          name = "kafka", image = "apache/kafka:3.9.1", port = 9092, secrets = {}
          env = {
            KAFKA_NODE_ID                                  = "1"
            KAFKA_PROCESS_ROLES                            = "broker,controller"
            KAFKA_LISTENERS                                = "PLAINTEXT://:9092,CONTROLLER://:9093"
            KAFKA_ADVERTISED_LISTENERS                     = "PLAINTEXT://kafka.${local.namespace}:9092"
            KAFKA_LISTENER_SECURITY_PROTOCOL_MAP           = "PLAINTEXT:PLAINTEXT,CONTROLLER:PLAINTEXT"
            KAFKA_CONTROLLER_LISTENER_NAMES                = "CONTROLLER"
            KAFKA_CONTROLLER_QUORUM_VOTERS                 = "1@localhost:9093"
            KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR         = "1"
            KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR = "1"
            KAFKA_TRANSACTION_STATE_LOG_MIN_ISR            = "1"
            KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS         = "0"
            KAFKA_AUTO_CREATE_TOPICS_ENABLE                = "true"
            KAFKA_NUM_PARTITIONS                           = "3"
            KAFKA_HEAP_OPTS                                = "-Xmx512m -Xms512m"
          }
        }]
      }
      presidio = {
        cpu        = 512, memory = 2048, role = "plain", lb = null
        containers = [{ name = "presidio", image = "mcr.microsoft.com/presidio-analyzer:latest", port = 3000, env = {}, secrets = {} }]
      }
      workbench = {
        cpu = 256, memory = 512, role = "plain", lb = "workbench"
        containers = [{ name = "workbench", image = local.img.workbench, port = 80, secrets = {},
        env = { API_UPSTREAM = "api.${local.namespace}:8000", DNS_RESOLVER = "169.254.169.253" } }]
      }
      airflow = {
        cpu = 1024, memory = 4096, role = "agents", lb = "airflow"
        containers = [{
          name = "airflow", image = local.img.airflow, command = ["standalone"], port = 8080
          env = merge(local.sentinel_env, {
            AIRFLOW__CORE__EXECUTOR                       = "LocalExecutor"
            AIRFLOW__CORE__LOAD_EXAMPLES                  = "False"
            AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_ALL_ADMINS = "True" # demo: behind the CloudFront IP allow-list
            AIRFLOW__API__BASE_URL                        = local.urls.airflow
            # Task runners call the execution API inside the container, not through CloudFront (IP allow-list)
            AIRFLOW__CORE__EXECUTION_API_SERVER_URL = "http://localhost:8080/execution/"
          })
          secrets = merge(local.sentinel_secrets, { AIRFLOW__DATABASE__SQL_ALCHEMY_CONN = "airflow_dsn" })
        }]
      }
      observability = {
        cpu = 1024, memory = 2048, role = "plain", lb = "grafana"
        containers = [
          { name = "otel", image = local.img.otel, port = 4318, env = { TEMPO_OTLP_ENDPOINT = "localhost:4327" }, secrets = {} },
          { name = "tempo", image = local.img.tempo, port = 3200, env = { TEMPO_OTLP_PORT = "4327" }, secrets = {} },
          { name = "prometheus", image = local.img.prometheus, port = 9090, env = {}, secrets = {} },
          {
            name = "grafana", image = local.img.grafana, port = 3000, secrets = { SENTINEL_DB_PASSWORD = "pg_password" }
            env = {
              GF_AUTH_ANONYMOUS_ENABLED = "true", GF_AUTH_ANONYMOUS_ORG_ROLE = "Editor", GF_AUTH_DISABLE_LOGIN_FORM = "true"
              GF_SERVER_ROOT_URL        = local.urls.grafana
              PROMETHEUS_URL            = "http://localhost:9090", TEMPO_URL = "http://localhost:3200"
              SENTINEL_DB_HOST          = "${local.db_host}:5432", SENTINEL_DB_USER = "sentinel", SENTINEL_DB_SSLMODE = "require"
            }
          },
        ]
      }
    },
    {
      for s in local.mcp_servers : "mcp-${replace(s, "_", "-")}" => {
        cpu        = 256, memory = 512, role = s == "policy_kb" ? "agents" : "plain", lb = null
        containers = [{ name = "mcp", image = local.img.mcp, command = [s], port = 8000, env = local.mcp_env, secrets = local.mcp_secrets }]
      }
    },
  )
}

locals {
  container_definitions = {
    for svc, s in local.services : svc => [
      for c in s.containers : merge(
        {
          name         = c.name
          image        = c.image
          essential    = true
          portMappings = [{ containerPort = c.port, protocol = "tcp" }]
          environment  = [for k, v in c.env : { name = k, value = v }]
          secrets = concat([for k, v in c.secrets : { name = k, valueFrom = "${local.secret}:${v}::" }],
          [for k, v in try(c.whole_secrets, {}) : { name = k, valueFrom = v }])
          logConfiguration = {
            logDriver = "awslogs"
            options = {
              awslogs-group         = aws_cloudwatch_log_group.main.name
              awslogs-region        = var.region
              awslogs-stream-prefix = svc
            }
          }
        },
        try({ command = c.command }, {}),
      )
    ]
  }
}

resource "aws_ecs_task_definition" "svc" {
  for_each                 = local.services
  family                   = "${var.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task[each.value.role].arn
  container_definitions    = jsonencode(local.container_definitions[each.key])
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
}

resource "aws_service_discovery_service" "svc" {
  for_each = local.services
  name     = each.key
  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.main.id
    dns_records {
      type = "A"
      ttl  = 10
    }
  }
  force_destroy = true
}

locals {
  lb_container = { workbench = ["workbench", 80], grafana = ["grafana", 3000], airflow = ["airflow", 8080] }
}

resource "aws_ecs_service" "svc" {
  for_each                           = local.services
  name                               = each.key
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.svc[each.key].arn
  desired_count                      = 1
  launch_type                        = "FARGATE"
  deployment_minimum_healthy_percent = 0
  health_check_grace_period_seconds  = each.value.lb == null ? null : 300
  enable_execute_command             = false

  network_configuration {
    subnets         = aws_subnet.private[*].id
    security_groups = [aws_security_group.tasks.id]
  }

  service_registries {
    registry_arn = aws_service_discovery_service.svc[each.key].arn
  }

  dynamic "load_balancer" {
    for_each = each.value.lb == null ? [] : [each.value.lb]
    content {
      target_group_arn = aws_lb_target_group.app[load_balancer.value].arn
      container_name   = local.lb_container[load_balancer.value][0]
      container_port   = local.lb_container[load_balancer.value][1]
    }
  }

  depends_on = [aws_lb_listener.app]
}

# One-off tasks (infra/aws/bootstrap.sh): `sentinel kafka init`, `sentinel data load|kb|graph`, Airflow DB
resource "aws_ecs_task_definition" "task" {
  family                   = "${var.name}-task"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 2048
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task["agents"].arn
  container_definitions = jsonencode([merge(local.container_definitions["api"][0], {
    name       = "task"
    entryPoint = ["sh", "-c"]
    command    = ["sentinel --help"]
    logConfiguration = merge(local.container_definitions["api"][0].logConfiguration, {
      options = merge(local.container_definitions["api"][0].logConfiguration.options, { awslogs-stream-prefix = "task" })
    })
  })])
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
}
