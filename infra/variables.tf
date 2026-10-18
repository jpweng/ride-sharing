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
  description = "Public hostname for the app, e.g. rides.example.ch (DNS managed outside AWS; point an A record at the server_public_ip output). Empty: plain HTTP on the server's IP. Set: Caddy gets a Let's Encrypt certificate and serves HTTPS."
  type        = string
  default     = ""
}

variable "instance_type" {
  description = "EC2 instance type. Must be arm64 (Graviton): CI builds arm64 images."
  type        = string
  default     = "t4g.micro" # 2 vCPU (burstable), 1 GiB RAM
}

variable "data_volume_size_gb" {
  description = "Size of the EBS volume holding the SQLite database and Caddy's certificates."
  type        = number
  default     = 4
}

variable "log_retention_days" {
  description = "CloudWatch log retention for container logs."
  type        = number
  default     = 14
}
