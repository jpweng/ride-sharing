# DNS for domain_name. The domain is registered elsewhere (rideandrail.ch at
# Hostpoint); its nameservers there must be set to this zone's name_servers
# output.

resource "aws_route53_zone" "site" {
  count = local.https_enabled ? 1 : 0

  name = var.domain_name
}

# Apex and www both point at the server; Caddy redirects www to the apex.
resource "aws_route53_record" "site" {
  for_each = local.https_enabled ? toset([var.domain_name, "www.${var.domain_name}"]) : toset([])

  zone_id = aws_route53_zone.site[0].zone_id
  name    = each.value
  type    = "A"
  ttl     = 300
  records = [aws_eip.app.public_ip]
}

# Mail: only Amazon SES sends as the domain (email.tf), and only signed
# (DKIM) mail from it may be accepted. SES uses mail.<domain> as its MAIL FROM
# domain, which has its own SPF record (email.tf); the apex record below
# covers SES's fallback and says that nothing else sends as the domain.
resource "aws_route53_record" "spf" {
  count = local.https_enabled ? 1 : 0

  zone_id = aws_route53_zone.site[0].zone_id
  name    = var.domain_name
  type    = "TXT"
  ttl     = 3600
  records = ["v=spf1 include:amazonses.com -all"]
}

# Reject mail that fails both aligned DKIM and aligned SPF: SES signs with
# the domain's DKIM keys, so its mail passes; spoofed mail doesn't.
resource "aws_route53_record" "dmarc" {
  count = local.https_enabled ? 1 : 0

  zone_id = aws_route53_zone.site[0].zone_id
  name    = "_dmarc.${var.domain_name}"
  type    = "TXT"
  ttl     = 3600
  records = ["v=DMARC1; p=reject; adkim=r; aspf=r"]
}

# Renamed from when the domain sent no mail; updated in place.
moved {
  from = aws_route53_record.no_mail_spf
  to   = aws_route53_record.spf
}

moved {
  from = aws_route53_record.no_mail_dmarc
  to   = aws_route53_record.dmarc
}
