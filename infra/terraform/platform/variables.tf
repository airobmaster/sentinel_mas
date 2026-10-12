variable "region" {
  type    = string
  default = "eu-west-2"
}

variable "name" {
  type    = string
  default = "sentinel"
}

variable "allowed_ips" {
  description = "Viewer IPv4 addresses allowed through CloudFront (the demo machines)"
  type        = list(string)
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "neo4j_uri" {
  description = "Neo4j AuraDB Free, e.g. neo4j+s://xxxx.databases.neo4j.io"
  type        = string
}

variable "neo4j_user" {
  type    = string
  default = "neo4j"
}

variable "neo4j_password" {
  description = "Written to Secrets Manager only (write-only); pass with TF_VAR_neo4j_password"
  type        = string
  sensitive   = true
  ephemeral   = true
  default     = null
}

variable "secrets_version" {
  description = "Bump to write new generated passwords and the Neo4j password to Aurora and Secrets Manager"
  type        = number
  default     = 1
}

variable "github_repo_with_ids" {
  description = "GitHub OIDC subject of the repository allowed to deploy (owner@id/name@id)"
  type        = string
  default     = "airobmaster@201773947/sentinel_mas@1406359223"
}
