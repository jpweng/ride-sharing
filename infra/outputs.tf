output "app_url" {
  value = local.https_enabled ? "https://${var.domain_name}" : "http://${aws_eip.app.public_ip}"
}

output "server_public_ip" {
  description = "Stable Elastic IP; domain_name and its www point at it."
  value       = aws_eip.app.public_ip
}

output "name_servers" {
  description = "Set these as the domain's nameservers at its registrar (Hostpoint)."
  value       = local.https_enabled ? aws_route53_zone.site[0].name_servers : []
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

output "ecr_routing_repository_url" {
  value = aws_ecr_repository.routing.repository_url
}

output "github_deploy_user" {
  description = "IAM user whose access key goes into the GitHub secrets AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY."
  value       = aws_iam_user.github_deploy.name
}

output "github_actions_variables" {
  description = "Set these as repository variables (Settings > Secrets and variables > Actions > Variables)."
  value = {
    AWS_REGION                 = var.aws_region
    ECR_REPOSITORY_URL         = aws_ecr_repository.app.repository_url
    ECR_PROXY_REPOSITORY_URL   = aws_ecr_repository.proxy.repository_url
    ECR_ROUTING_REPOSITORY_URL = aws_ecr_repository.routing.repository_url # routing/, monthly
    SERVER_NAME                = var.project
    LOG_GROUP                  = aws_cloudwatch_log_group.app.name
    # Caddy site addresses: ":80" serves plain HTTP on any hostname/IP; domain
    # names make Caddy obtain certificates and redirect HTTP to HTTPS.
    SITE_ADDRESS = local.https_enabled ? "${var.domain_name}, www.${var.domain_name}" : ":80"
    # Sign-in emails (backend/mailer.py): the sender, and the site's address
    # for the links in them. Without a domain there is no SES identity, so
    # nobody can sign in by email (the demo-driver view still works).
    EMAIL_FROM      = local.email_enabled ? local.email_from : ""
    PUBLIC_BASE_URL = local.https_enabled ? "https://${var.domain_name}" : "http://${aws_eip.app.public_ip}"
    # Profile photos (photos.tf). Leave it unset and the site offers no photos.
    PHOTO_BUCKET = aws_s3_bucket.photos.bucket
  }
}
