variable "region" {
  type    = string
  default = "eu-west-2"
}

variable "name" {
  type    = string
  default = "sentinel-aml-demo"
}

variable "domain_prefix" {
  description = "Hosted sign-in page prefix; unique per region (lowercase, digits, hyphens)"
  type        = string
  default     = "sentinel-aml-demo"
}

variable "roles" {
  description = "Cognito groups = API roles"
  type        = map(string)
  default = {
    l1    = "L1 analyst: decides fast-lane cases (BR-08), attaches customer replies"
    l2    = "L2 investigator: decides any case, approves customer information requests (UC-03)"
    qa    = "QA reviewer: samples and labels decided cases (UC-05)"
    sme   = "Subject-matter expert: approves lessons (backlog)"
    admin = "Administrator: starts cases manually, attaches customer replies"
  }
}

variable "workbench_callback_urls" {
  type    = list(string)
  default = ["http://localhost:5173/callback"]
}

variable "workbench_logout_urls" {
  type    = list(string)
  default = ["http://localhost:5173/"]
}
