output "app_url" {
  value = local.https_enabled ? "https://${var.domain_name}" : "http://${aws_eip.app.public_ip}"
}

output "server_public_ip" {
  description = "Stable Elastic IP. With a domain, point an A record for domain_name at it."
  value       = aws_eip.app.public_ip
}

output "server_instance_id" {
  description = "For a shell: `aws ssm start-session --target <id>`, or EC2 console > Connect > Session Manager."
  value       = aws_instance.app.id
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "ecr_proxy_repository_url" {
  value = aws_ecr_repository.proxy.repository_url
}

output "github_deploy_user" {
  description = "IAM user whose access key goes into the GitHub secrets AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY."
  value       = aws_iam_user.github_deploy.name
}

output "github_actions_variables" {
  description = "Set these as repository variables (Settings > Secrets and variables > Actions > Variables)."
  value = {
    AWS_REGION               = var.aws_region
    ECR_REPOSITORY_URL       = aws_ecr_repository.app.repository_url
    ECR_PROXY_REPOSITORY_URL = aws_ecr_repository.proxy.repository_url
    SERVER_NAME              = var.project
    LOG_GROUP                = aws_cloudwatch_log_group.app.name
    # Caddy site address: ":80" serves plain HTTP on any hostname/IP; a domain
    # name makes Caddy obtain a certificate and redirect HTTP to HTTPS.
    SITE_ADDRESS = local.https_enabled ? var.domain_name : ":80"
  }
}
