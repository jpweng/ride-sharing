variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "eu-north-1" # Stockholm, where the AWS project lives
}

variable "project" {
  description = "Name prefix for all resources."
  type        = string
  default     = "ride-sharing"
}

variable "domain_name" {
  description = "Public hostname for the app, e.g. rides.example.ch (DNS managed outside AWS). Empty: serve plain HTTP on the ALB's own DNS name, no certificate."
  type        = string
  default     = ""
}

variable "image_tag" {
  description = "Image tag in the task definition Terraform registers. CI deploys by commit SHA and ignores this after the first run."
  type        = string
  default     = "latest"
}

variable "cpu" {
  description = "Fargate task CPU units (256 = 0.25 vCPU)."
  type        = number
  default     = 256
}

variable "memory" {
  description = "Fargate task memory in MiB."
  type        = number
  default     = 512
}

variable "log_retention_days" {
  description = "CloudWatch log retention for container logs."
  type        = number
  default     = 14
}
