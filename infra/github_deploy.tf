# IAM user for GitHub Actions deploys.
#
# The project's managed SCP denies iam:*Provider*, so GitHub OIDC (keyless)
# isn't available; a narrowly scoped user with an access key is the fallback.
# The access key is deliberately NOT created here, so the secret never lands
# in Terraform state. Create it with the AWS CLI and pipe it straight into
# GitHub secrets (see README, "Deploying to AWS").

resource "aws_iam_user" "github_deploy" {
  name = "${var.project}-github-deploy"
}

data "aws_iam_policy_document" "github_deploy" {
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "EcrPush"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:DescribeImages",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
    ]
    resources = [aws_ecr_repository.app.arn, aws_ecr_repository.proxy.arn]
  }

  statement {
    sid       = "FindServer"
    actions   = ["ec2:DescribeInstances"]
    resources = ["*"]
  }

  # Run Command needs the permission on both the document and the target
  # instance; the instance side is limited to the app server by its Name tag.
  statement {
    sid       = "RunShellScriptDocument"
    actions   = ["ssm:SendCommand"]
    resources = ["arn:aws:ssm:${var.aws_region}::document/AWS-RunShellScript"]
  }

  statement {
    sid       = "RunOnAppServerOnly"
    actions   = ["ssm:SendCommand"]
    resources = ["arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/*"]

    condition {
      test     = "StringEquals"
      variable = "ssm:resourceTag/Name"
      values   = [var.project]
    }
  }

  statement {
    sid       = "ReadCommandResult"
    actions   = ["ssm:GetCommandInvocation"]
    resources = ["*"]
  }
}

resource "aws_iam_user_policy" "github_deploy" {
  name   = "deploy"
  user   = aws_iam_user.github_deploy.name
  policy = data.aws_iam_policy_document.github_deploy.json
}
