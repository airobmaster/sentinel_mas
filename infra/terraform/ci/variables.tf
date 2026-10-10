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
