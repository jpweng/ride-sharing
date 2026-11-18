resource "aws_ecr_repository" "app" {
  name = var.project

  # Mutable so CI can move the `latest` tag; deploys pin the commit-SHA tag.
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

# Caddy reverse proxy (TLS + rate limiting), built from deploy/caddy/.
resource "aws_ecr_repository" "proxy" {
  name = "${var.project}-proxy"

  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

# Routing (OSRM + Switzerland data), built monthly from routing/ by the
# "Routing data" workflow. A few hundred MB per image: keep only the last 3.
resource "aws_ecr_repository" "routing" {
  name = "${var.project}-routing"

  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "routing" {
  repository = aws_ecr_repository.routing.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 3 most recent routing images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 3
      }
      action = {
        type = "expire"
      }
    }]
  })
}

resource "aws_ecr_lifecycle_policy" "keep_recent" {
  for_each = {
    app   = aws_ecr_repository.app.name
    proxy = aws_ecr_repository.proxy.name
  }

  repository = each.value

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 10 most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = {
        type = "expire"
      }
    }]
  })
}

moved {
  from = aws_ecr_lifecycle_policy.app
  to   = aws_ecr_lifecycle_policy.keep_recent["app"]
}
