output "app_url" {
  value = local.https_enabled ? "https://${var.domain_name}" : "http://${aws_lb.app.dns_name}"
}

output "alb_dns_name" {
  description = "Point a CNAME for domain_name at this, at your DNS provider."
  value       = aws_lb.app.dns_name
}

output "acm_validation_records" {
  description = "Add these CNAME records at your DNS provider to validate the certificate (empty without a domain)."
  value = [
    for o in flatten(aws_acm_certificate.app[*].domain_validation_options) : {
      name  = o.resource_record_name
      type  = o.resource_record_type
      value = o.resource_record_value
    }
  ]
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  value = aws_ecs_service.app.name
}

output "task_family" {
  value = aws_ecs_task_definition.app.family
}

output "github_deploy_user" {
  description = "IAM user whose access key goes into the GitHub secrets AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY."
  value       = aws_iam_user.github_deploy.name
}

# For one-off tasks (e.g. seeding) via `aws ecs run-task`.
output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "task_security_group_id" {
  value = aws_security_group.task.id
}

output "github_actions_variables" {
  description = "Set these as repository variables (Settings > Secrets and variables > Actions > Variables)."
  value = {
    AWS_REGION         = var.aws_region
    ECR_REPOSITORY_URL = aws_ecr_repository.app.repository_url
    ECS_CLUSTER        = aws_ecs_cluster.main.name
    ECS_SERVICE        = aws_ecs_service.app.name
    TASK_FAMILY        = aws_ecs_task_definition.app.family
  }
}
