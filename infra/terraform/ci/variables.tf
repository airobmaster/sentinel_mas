variable "region" {
  type    = string
  default = "eu-west-2"
}

variable "name" {
  type    = string
  default = "sentinel-aml-demo"
}

variable "github_repo" {
  description = "owner/name of the repository whose workflows may assume the role"
  type        = string
  default     = "airobmaster/sentinel_mas"
}

variable "github_repo_with_ids" {
  description = "The same repository as GitHub's OIDC subject names it: owner@owner_id/name@repo_id"
  type        = string
  default     = "airobmaster@201773947/sentinel_mas@1406359223"
}
