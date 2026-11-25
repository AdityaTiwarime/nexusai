# The only way in is the website: HTTPS (plus HTTP, which Caddy redirects to HTTPS and
# Let's Encrypt uses to verify the domain). There is no SSH port: administrators use
# AWS Systems Manager Session Manager, which is authenticated with IAM and logged.
resource "aws_security_group" "web" {
  name_prefix = "${var.project}-web-"
  description = "NexusAI web server: HTTPS in, HTTPS out"
  vpc_id      = aws_vpc.main.id

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  security_group_id = aws_security_group.web.id
  description       = "HTTPS from anywhere"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "http3" {
  security_group_id = aws_security_group.web.id
  description       = "HTTP/3 (QUIC) from anywhere"
  ip_protocol       = "udp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "http" {
  #checkov:skip=CKV_AWS_260:Port 80 only redirects to HTTPS and answers Let's Encrypt HTTP-01 challenges.
  security_group_id = aws_security_group.web.id
  description       = "HTTP from anywhere (redirect to HTTPS, ACME challenge)"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

# Outbound: HTTPS only (container images, Let's Encrypt, AWS APIs, OpenAI, connected
# systems). DNS and time sync use the VPC's own services, which security groups don't filter.
resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.web.id
  description       = "HTTPS to anywhere"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}
