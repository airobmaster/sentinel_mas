# Edge: CloudFront (HTTPS on *.cloudfront.net, IP allow-list) -> ALB (HTTP, only CloudFront, and only with
# the shared origin header) -> workbench, Grafana, Airflow. One distribution and ALB port per app.

locals {
  apps = {
    workbench = { port = 80, health = "/" }
    grafana   = { port = 8081, health = "/api/health" }
    airflow   = { port = 8082, health = "/api/v2/monitor/health" }
  }
  target_port = { workbench = 80, grafana = 3000, airflow = 8080 }
}

resource "random_password" "origin_header" {
  length  = 32
  special = false
}

resource "aws_lb" "main" {
  name               = var.name
  load_balancer_type = "application"
  subnets            = aws_subnet.public[*].id
  security_groups    = [aws_security_group.alb.id]
  idle_timeout       = 3600 # live progress (SSE) streams
}

resource "aws_lb_target_group" "app" {
  for_each    = local.apps
  name        = "${var.name}-${each.key}"
  port        = local.target_port[each.key]
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.main.id
  health_check {
    path                = each.value.health
    matcher             = "200-399"
    interval            = 30
    unhealthy_threshold = 5
  }
  deregistration_delay = 10
}

resource "aws_lb_listener" "app" {
  for_each          = local.apps
  load_balancer_arn = aws_lb.main.arn
  port              = each.value.port
  protocol          = "HTTP"
  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Forbidden"
      status_code  = "403"
    }
  }
}

resource "aws_lb_listener_rule" "app" {
  for_each     = local.apps
  listener_arn = aws_lb_listener.app[each.key].arn
  priority     = 1
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app[each.key].arn
  }
  condition {
    http_header {
      http_header_name = "X-Sentinel-Origin"
      values           = [random_password.origin_header.result]
    }
  }
}

resource "aws_cloudfront_function" "allow_list" {
  name    = "${var.name}-allow-list"
  runtime = "cloudfront-js-2.0"
  comment = "Only the demo machines"
  publish = true
  code    = <<-EOT
    var ALLOWED = ${jsonencode(var.allowed_ips)};
    function handler(event) {
      if (ALLOWED.indexOf(event.viewer.ip) === -1) {
        return { statusCode: 403, statusDescription: 'Forbidden' };
      }
      return event.request;
    }
  EOT
}

resource "aws_cloudfront_distribution" "app" {
  for_each        = local.apps
  enabled         = true
  comment         = "${var.name} ${each.key}"
  is_ipv6_enabled = false # the allow-list holds IPv4 addresses
  price_class     = "PriceClass_100"

  origin {
    origin_id   = "alb"
    domain_name = aws_lb.main.dns_name
    custom_origin_config {
      http_port              = each.value.port
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
      origin_read_timeout    = 60
    }
    custom_header {
      name  = "X-Sentinel-Origin"
      value = random_password.origin_header.result
    }
  }

  default_cache_behavior {
    target_origin_id         = "alb"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # Managed-CachingDisabled
    origin_request_policy_id = "216adef6-5c7f-47e4-b989-5492eafa07d3" # Managed-AllViewer
    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.allow_list.arn
    }
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }
  viewer_certificate {
    cloudfront_default_certificate = true
  }
}
