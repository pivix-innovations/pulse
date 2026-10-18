"""Build the Lambda zip and deploy deploy/template.yaml.

Usage: python deploy/deploy.py [--no-domain]
Needs AWS creds in env (AWS_ACCESS_KEY_ID/SECRET) and a .env prepared by deploy/configure_env.py.
"""
import os, secrets, shutil, subprocess, sys, time, zipfile, pathlib
import boto3
from dotenv import dotenv_values, load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
REGION = "ap-south-1"
STACK = "pulse-dev"
session = boto3.Session(region_name=REGION)
sts, s3, cfn, acm = (session.client(n) for n in ("sts", "s3", "cloudformation", "acm"))
DOMAIN = "dev-pulse-api.pivix.in"
account = sts.get_caller_identity()["Account"]
bucket = f"quotient-dev-deploy-{account}-{REGION}"  # shared deploy bucket (created by the quotient deploy)

sec = dotenv_values(ROOT / "undread/pulse-secrets.env")   # created by deploy/configure_env.py
env = dotenv_values(ROOT / ".env")

# build
build = ROOT / ".build"
shutil.rmtree(build, ignore_errors=True)
subprocess.check_call([
    os.path.expanduser("~/.local/bin/uv"), "pip", "install", "-q", "--python-platform", "x86_64-manylinux_2_28", "--python-version", "3.12", "--target", str(build), "-r", str(ROOT / "deploy/requirements-lambda.txt"),
    "--only-binary", ":all:",
])
for d in ("api", "clients", "config", "database", "domain", "jobqueue", "providers", "renderers",
          "repositories", "services", "sse", "telemetry", "workers", "workflows"):
    shutil.copytree(ROOT / d, build / d, ignore=shutil.ignore_patterns("__pycache__", "tests"))
shutil.copy(ROOT / "main.py", build / "main.py")
for junk in build.rglob("__pycache__"):
    shutil.rmtree(junk, ignore_errors=True)
zpath = ROOT / ".build.zip"
with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
    for f in build.rglob("*"):
        if f.is_file():
            z.write(f, f.relative_to(build))
print(f"zip: {zpath.stat().st_size/1e6:.1f} MB")
key = f"pulse-{int(time.time())}.zip"
s3.upload_file(str(zpath), bucket, key)

# certificate (DNS-validated; add the printed CNAME at the DNS host). --no-domain skips it.
cert_arn = ""
if "--no-domain" not in sys.argv:
    for c in acm.list_certificates()["CertificateSummaryList"]:
        if c["DomainName"] == DOMAIN:
            cert_arn = c["CertificateArn"]
    if not cert_arn:
        cert_arn = acm.request_certificate(DomainName=DOMAIN, ValidationMethod="DNS")["CertificateArn"]
    while True:
        d = acm.describe_certificate(CertificateArn=cert_arn)["Certificate"]
        if d["Status"] == "ISSUED":
            break
        rr = (d.get("DomainValidationOptions") or [{}])[0].get("ResourceRecord")
        print(f"cert {d['Status']}; add CNAME {rr['Name']} -> {rr['Value']}" if rr else "waiting for validation record...")
        time.sleep(30)

p = lambda k, v: {"ParameterKey": k, "ParameterValue": v}
ingress = env.get("PULSE_SECURITY__INGRESS_API_KEYS") or "{}"
params = [
    p("CodeBucket", bucket), p("CodeKey", key), p("DbHost", env["PULSE_DATABASE__HOST"]), p("DbUser", env["PULSE_DATABASE__USERNAME"]),
    p("DbPassword", env["PULSE_DATABASE__PASSWORD"]), p("DbName", env["PULSE_DATABASE__DB"]),
    p("RedisHost", env["PULSE_REDIS__HOST"]), p("RedisPort", env["PULSE_REDIS__PORT"]), p("RedisUser", env.get("PULSE_REDIS__USERNAME") or "default"),
    p("RedisPassword", env["PULSE_REDIS__PASSWORD"]),
    p("SecretsKey", sec["PULSE_SECURITY__SECRETS_KEY"]), p("UnsubscribeKey", sec["PULSE_SECURITY__UNSUBSCRIBE_KEY"]),
    p("IngressApiKeys", ingress), p("SentinelBaseUrl", env.get("PULSE_SENTINEL__BASE_URL", "")),
    p("CertificateArn", cert_arn), p("DomainName", DOMAIN),
]
kw = dict(StackName=STACK, TemplateBody=(ROOT / "deploy/template.yaml").read_text(), Parameters=params, Capabilities=["CAPABILITY_IAM"])
try:
    cfn.describe_stacks(StackName=STACK)
    op, waiter = cfn.update_stack, "stack_update_complete"
except cfn.exceptions.ClientError:
    op, waiter = cfn.create_stack, "stack_create_complete"
try:
    op(**kw)
    cfn.get_waiter(waiter).wait(StackName=STACK, WaiterConfig={"Delay": 10, "MaxAttempts": 90})
except cfn.exceptions.ClientError as e:
    if "No updates" not in str(e):
        raise
for o in cfn.describe_stacks(StackName=STACK)["Stacks"][0].get("Outputs", []):
    print(f"{o['OutputKey']}: {o['OutputValue']}")
