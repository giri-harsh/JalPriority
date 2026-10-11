"""M12: infra/template.yaml (SAM), infra/samconfig.toml and the SageMaker entry point, validated offline."""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from jp_a.config import Settings

from .ml_helpers import write_synthetic_split
from .test_aws_fakes import FakeS3Client, client_error

MEMBER_A = Path(__file__).resolve().parents[2]
INFRA = MEMBER_A / "infra"
TEMPLATE = INFRA / "template.yaml"
PSEUDO_PARAMETERS = {
    "AWS::AccountId", "AWS::Region", "AWS::StackName", "AWS::Partition", "AWS::URLSuffix", "AWS::StackId", "AWS::NoValue",
}  # fmt: skip


# ----------------------------------------------------------------------------- CloudFormation-tolerant YAML

class CfnLoader(yaml.SafeLoader):
    """SafeLoader that turns ``!Ref x`` / ``!Sub ...`` / ``!GetAtt a.b`` into ``{"Ref": ...}`` style mappings."""


def _cfn_tag(loader: CfnLoader, suffix: str, node: yaml.Node) -> dict[str, Any]:
    if isinstance(node, yaml.ScalarNode):
        value: Any = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    key = suffix if suffix == "Ref" or suffix == "Condition" else f"Fn::{suffix}"
    return {key: value}


CfnLoader.add_multi_constructor("!", _cfn_tag)


def load_template() -> dict[str, Any]:
    return yaml.load(TEMPLATE.read_text(encoding="utf-8"), Loader=CfnLoader)  # noqa: S506 - tolerant SafeLoader subclass


@pytest.fixture(scope="module")
def template() -> dict[str, Any]:
    return load_template()


def resources_of(template: dict[str, Any], type_name: str) -> dict[str, dict[str, Any]]:
    return {k: v for k, v in template["Resources"].items() if v["Type"] == type_name}


def walk(node: Any):
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def as_text(node: Any) -> str:
    return json.dumps(node, sort_keys=True)


# --------------------------------------------------------------------------------------------- structure

def test_template_parses_and_is_a_sam_template(template):
    assert template["AWSTemplateFormatVersion"] == "2010-09-09"
    assert template["Transform"] == "AWS::Serverless-2016-10-31"
    assert {"Parameters", "Resources", "Outputs"} <= set(template)


def test_required_resources_exist(template):
    types = {v["Type"] for v in template["Resources"].values()}
    assert {
        "AWS::S3::Bucket", "AWS::IAM::Role", "AWS::Serverless::Function", "AWS::Serverless::HttpApi",
    } <= types  # fmt: skip
    roles = resources_of(template, "AWS::IAM::Role")
    principals = {
        name: role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]["Principal"]["Service"]
        for name, role in roles.items()
    }
    assert sorted(principals.values()) == ["lambda.amazonaws.com", "sagemaker.amazonaws.com"]


def test_every_reference_resolves(template):
    known = set(template["Resources"]) | set(template["Parameters"]) | PSEUDO_PARAMETERS
    for node in walk(template):
        if not isinstance(node, dict) or len(node) != 1:
            continue
        (key, value), = node.items()
        if key == "Ref":
            assert value in known, f"unknown Ref {value}"
        elif key == "Fn::GetAtt":
            name = value.split(".")[0] if isinstance(value, str) else value[0]
            assert name in template["Resources"], f"unknown GetAtt {value}"
        elif key == "Fn::Sub":
            text = value if isinstance(value, str) else value[0]
            for variable in re.findall(r"\$\{([^}!]+)\}", text):
                assert variable.split(".")[0] in known, f"unknown Sub variable {variable}"
    for output in template["Outputs"].values():
        assert output["Value"]


def test_stack_name_is_fixed_in_samconfig_and_used_by_the_function():
    config = tomllib.loads((INFRA / "samconfig.toml").read_text(encoding="utf-8"))
    deploy = config["default"]["deploy"]["parameters"]
    assert deploy["stack_name"] == "jalpriority-a" == config["default"]["global"]["parameters"]["stack_name"]
    assert deploy["region"] == "ap-south-1" == Settings().a_aws_region
    assert "CAPABILITY_IAM" in deploy["capabilities"]


# ------------------------------------------------------------------------------------------------ bucket

def test_bucket_name_and_hardening(template):
    (bucket,) = resources_of(template, "AWS::S3::Bucket").values()
    props = bucket["Properties"]
    assert props["BucketName"] == {"Fn::Sub": "jalpriority-a-${AWS::AccountId}"}
    assert all(props["PublicAccessBlockConfiguration"].values())
    assert props["BucketEncryption"]["ServerSideEncryptionConfiguration"]
    assert bucket["DeletionPolicy"] == "Retain"


def test_the_four_prefixes_are_documented_and_used(template):
    text = TEMPLATE.read_text(encoding="utf-8")
    for prefix in ("a/scenarios/", "a/derived/", "a/models/", "a/training/"):
        assert prefix in text, prefix
    function = template["Resources"]["ApiFunction"]["Properties"]["Environment"]["Variables"]
    assert function["A_S3_PREFIX"] == "a/" == Settings().a_s3_prefix


# ----------------------------------------------------------------------------------------------- Lambda

def test_function_is_the_container_image_with_the_specified_size_and_timeout(template):
    functions = resources_of(template, "AWS::Serverless::Function")
    assert list(functions) == ["ApiFunction"]
    props = functions["ApiFunction"]["Properties"]
    assert props["PackageType"] == "Image"
    assert props["MemorySize"] == 2048 and props["Timeout"] == 15
    docker = functions["ApiFunction"]["Metadata"]
    context = (INFRA / docker["DockerContext"]).resolve()  # SAM resolves the context relative to the template
    assert context == MEMBER_A.resolve()
    assert (context / docker["Dockerfile"]).resolve() == (INFRA / "lambda.Dockerfile").resolve()  # relative to context
    assert (INFRA / "lambda.Dockerfile").is_file()


def test_function_is_wired_to_the_http_api(template):
    events = template["Resources"]["ApiFunction"]["Properties"]["Events"]
    (event,) = events.values()
    assert event["Type"] == "HttpApi" and event["Properties"]["ApiId"] == {"Ref": "HttpApi"}
    assert event["Properties"]["Path"] == "/{proxy+}" and event["Properties"]["Method"] == "ANY"
    assert template["Resources"]["HttpApi"]["Type"] == "AWS::Serverless::HttpApi"
    assert "ApiBaseUrl" in template["Outputs"]


def test_solver_limit_leaves_headroom_inside_the_timeout(template):
    env = template["Resources"]["ApiFunction"]["Properties"]["Environment"]["Variables"]
    assert float(env["A_SOLVER_TIME_LIMIT_S"]) <= 15 - 5
    assert Settings(a_solver_time_limit_s=float(env["A_SOLVER_TIME_LIMIT_S"]))  # inside the allowed range


def test_environment_only_uses_a_and_shared_variables_that_settings_knows(template):
    env = template["Resources"]["ApiFunction"]["Properties"]["Environment"]["Variables"]
    known = {name.upper() for name in Settings.model_fields}
    for name in env:
        assert name.startswith(("A_", "SHARED_")), f"{name}: only A_* and SHARED_* variables are allowed"
        assert name in known, f"{name} is not a setting"
    assert env["A_AUTH_DISABLED"] == "false"
    assert env["SHARED_ENV"] == "aws"
    assert {"A_STORAGE_MODE", "A_MODEL_MODE", "A_S3_BUCKET", "A_SAGEMAKER_ENDPOINT", "A_AWS_REGION"} <= set(env)


def test_jwt_secret_has_no_default_and_is_not_echoed(template):
    secret = template["Parameters"]["SharedJwtSecret"]
    assert secret["NoEcho"] is True and "Default" not in secret
    assert template["Parameters"]["SharedJwtIssuer"]["Default"] == Settings().shared_jwt_issuer
    assert template["Parameters"]["SharedJwtAudience"]["Default"] == Settings().shared_jwt_audience
    assert "dev-only" not in TEMPLATE.read_text(encoding="utf-8")


def test_mode_parameters_default_to_the_safe_choices(template):
    params = template["Parameters"]
    assert params["ModelMode"]["Default"] == "local"
    for name, field in (("StorageMode", "a_storage_mode"), ("ModelMode", "a_model_mode")):
        allowed = set(params[name]["AllowedValues"])
        assert allowed == set(Settings.model_fields[field].annotation.__args__)


# --------------------------------------------------------------------------------------------------- IAM

def statements(role: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for policy in role["Properties"]["Policies"]:
        out += policy["PolicyDocument"]["Statement"]
    return out


def listed(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


# actions whose resource cannot be narrowed in IAM (documented by AWS)
UNSCOPED_ACTIONS = {"ecr:GetAuthorizationToken", "cloudwatch:PutMetricData"}


def test_iam_policies_are_least_privilege(template):
    for name, role in resources_of(template, "AWS::IAM::Role").items():
        for statement in statements(role):
            assert statement["Effect"] == "Allow"
            actions = listed(statement["Action"])
            for action in actions:
                assert action != "*" and not action.endswith(":*"), f"{name}: wildcard action {action}"
            if "*" in listed(statement["Resource"]):
                assert set(actions) <= UNSCOPED_ACTIONS, f"{name}: Resource * for {actions}"


def test_lambda_role_can_only_touch_its_prefixes(template):
    role = template["Resources"]["ApiFunctionRole"]
    s3_statements = [s for s in statements(role) if any(a.startswith("s3:") for a in listed(s["Action"]))]
    for statement in s3_statements:
        for action in listed(statement["Action"]):
            assert action in {"s3:GetObject", "s3:PutObject", "s3:ListBucket"}
        for resource in listed(statement["Resource"]):
            text = as_text(resource)
            assert "ArtifactsBucket" in text
            if "s3:ListBucket" not in listed(statement["Action"]):
                assert re.search(r"/a/(scenarios|derived|models|training|truth)/\*|/a/labels\.json", text), text
    writes = [s for s in s3_statements if "s3:PutObject" in listed(s["Action"])]
    assert len(writes) == 1 and "a/derived/" in as_text(writes[0]["Resource"]), "only derived scenarios are writable"
    assert "sagemaker:InvokeEndpoint" in as_text(role)
    assert "endpoint/jp-a-*" in as_text(role), "SageMaker access must be limited to jp-a-* endpoints"


def test_sagemaker_role_is_scoped_to_training_and_models(template):
    role = template["Resources"]["SageMakerExecutionRole"]
    assert role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]["Action"] == "sts:AssumeRole"
    text = as_text(role)
    assert "a/training/" in text and "a/models/" in text
    assert "a/derived" not in text and "AdministratorAccess" not in text and "FullAccess" not in text
    assert "ManagedPolicyArns" not in role["Properties"]
    assert template["Outputs"]["SageMakerRoleArn"]["Value"] == {"Fn::GetAtt": "SageMakerExecutionRole.Arn"}


def test_bucket_policy_denies_plain_http_and_old_tls(template):
    (policy,) = resources_of(template, "AWS::S3::BucketPolicy").values()
    by_sid = {s["Sid"]: s for s in policy["Properties"]["PolicyDocument"]["Statement"]}
    assert set(by_sid) == {"DenyInsecureTransport", "DenyOldTls"}
    assert all(s["Effect"] == "Deny" for s in by_sid.values()), "the bucket policy only ever denies"
    assert by_sid["DenyInsecureTransport"]["Condition"] == {"Bool": {"aws:SecureTransport": "false"}}
    assert by_sid["DenyOldTls"]["Condition"] == {"NumericLessThan": {"s3:TlsVersion": 1.2}}


def all_statements(template: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [(name, s) for name, role in resources_of(template, "AWS::IAM::Role").items() for s in statements(role)]


def test_iam_has_no_negations_managed_policies_or_foreign_principals(template):
    for name, role in resources_of(template, "AWS::IAM::Role").items():
        assert "ManagedPolicyArns" not in role["Properties"], f"{name}: managed policies are broader than needed"
        assert len(role["Properties"]["Policies"]) == 1
    for name, statement in all_statements(template):
        assert not {"NotAction", "NotResource", "NotPrincipal", "Principal"} & set(statement), f"{name}: {statement}"
        assert statement.get("Sid"), f"{name}: every statement is named"
    text = as_text(template["Resources"])
    assert "iam:" not in text.replace("AWS::IAM::", ""), "no role may touch IAM"
    assert "arn:aws:s3:::" not in text and "s3:::*" not in text, "S3 is only addressed through the bucket resource"


def test_every_s3_statement_is_limited_to_the_artifacts_bucket(template):
    for name, statement in all_statements(template):
        actions = listed(statement["Action"])
        if not any(a.startswith("s3:") for a in actions):
            continue
        for resource in listed(statement["Resource"]):
            assert "ArtifactsBucket" in as_text(resource), f"{name}/{statement['Sid']}: {resource}"
        if "s3:ListBucket" not in actions:  # object-level access needs a key prefix (or the one labels.json key)
            for resource in listed(statement["Resource"]):
                assert re.search(r"/a/[a-z]+/\*\"\}$|/a/labels\.json\"\}$", as_text(resource)), resource


def test_bucket_listing_is_limited_to_the_key_prefixes(template):
    for role_name, allowed in (
        ("ApiFunctionRole", {"a/scenarios/*", "a/derived/*", "a/truth/*", "a/models/*", "a/training/*"}),
        ("SageMakerExecutionRole", {"a/training", "a/training/*", "a/models", "a/models/*"}),
    ):
        listings = [s for s in statements(template["Resources"][role_name]) if "s3:ListBucket" in listed(s["Action"])]
        assert len(listings) == 1
        condition = listings[0]["Condition"]
        # HeadBucket (the startup check) carries no prefix, so the key must be optional: ...IfExists
        assert set(condition) == {"StringLikeIfExists"}, condition
        assert set(condition["StringLikeIfExists"]["s3:prefix"]) == allowed


def test_lambda_role_writes_only_its_own_log_group(template):
    role = template["Resources"]["ApiFunctionRole"]
    (logs,) = [s for s in statements(role) if any(a.startswith("logs:") for a in listed(s["Action"]))]
    assert sorted(listed(logs["Action"])) == ["logs:CreateLogStream", "logs:PutLogEvents"]  # the group is made by CFN
    function_name = template["Resources"]["ApiFunction"]["Properties"]["FunctionName"]["Fn::Sub"]
    group_name = template["Resources"]["ApiLogGroup"]["Properties"]["LogGroupName"]["Fn::Sub"]
    assert group_name == f"/aws/lambda/{function_name}", "the log group must be the one the function writes to"
    assert as_text(logs["Resource"]).count(f"log-group:{group_name}:*") == 1
    log_group = template["Resources"]["ApiLogGroup"]
    assert log_group["Properties"]["RetentionInDays"] <= 30
    assert log_group["DeletionPolicy"] == "Delete" and log_group["UpdateReplacePolicy"] == "Delete"


def test_sagemaker_role_trust_and_images_are_scoped(template):
    role = template["Resources"]["SageMakerExecutionRole"]
    trust = role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
    assert trust["Condition"]["StringEquals"] == {"aws:SourceAccount": {"Ref": "AWS::AccountId"}}
    assert "sagemaker:${AWS::Region}:${AWS::AccountId}" in as_text(trust["Condition"]["ArnLike"]["aws:SourceArn"])
    by_sid = {s["Sid"]: s for s in statements(role)}
    assert "repository/jp-a-*" in as_text(by_sid["PullTrainingImage"]["Resource"])
    assert listed(by_sid["EcrLogin"]["Action"]) == ["ecr:GetAuthorizationToken"]
    assert by_sid["Metrics"]["Condition"] == {"StringLike": {"cloudwatch:namespace": "/aws/sagemaker/*"}}
    assert "log-group:/aws/sagemaker/*" in as_text(by_sid["Logs"]["Resource"])


def test_sagemaker_actions_on_the_lambda_role_are_read_or_invoke_only_and_jp_a_scoped(template):
    role = template["Resources"]["ApiFunctionRole"]
    allowed = {
        "sagemaker:InvokeEndpoint", "sagemaker:DescribeEndpoint", "sagemaker:DescribeEndpointConfig", "sagemaker:DescribeModel",
    }  # fmt: skip
    for statement in statements(role):
        sage = [a for a in listed(statement["Action"]) if a.startswith("sagemaker:")]
        assert set(sage) <= allowed
        if sage:
            assert all("/jp-a-*" in as_text(r) for r in listed(statement["Resource"]))


def test_http_api_is_throttled_and_cors_is_not_a_wildcard(template):
    api = template["Resources"]["HttpApi"]["Properties"]
    assert 0 < api["DefaultRouteSettings"]["ThrottlingRateLimit"] <= api["DefaultRouteSettings"]["ThrottlingBurstLimit"]
    assert api["CorsConfiguration"]["AllowOrigins"] == {"Fn::Split": [",", {"Ref": "CorsOrigins"}]}
    assert "*" not in template["Parameters"]["CorsOrigins"]["Default"]
    assert set(api["CorsConfiguration"]["AllowMethods"]) <= {"GET", "POST", "OPTIONS"}


def test_bucket_lifecycle_and_versioning(template):
    (bucket,) = resources_of(template, "AWS::S3::Bucket").values()
    props = bucket["Properties"]
    assert props["VersioningConfiguration"]["Status"] == "Enabled"
    rules = {r["Id"]: r for r in props["LifecycleConfiguration"]["Rules"]}
    assert rules["expire-derived-scenarios"]["Prefix"] == "a/derived/"
    assert rules["abort-incomplete-multipart-uploads"]["AbortIncompleteMultipartUpload"]["DaysAfterInitiation"] == 1
    assert bucket["UpdateReplacePolicy"] == "Retain"


def test_samconfig_resolves_repositories_and_can_deploy_the_iam_roles():
    deploy = tomllib.loads((INFRA / "samconfig.toml").read_text(encoding="utf-8"))["default"]["deploy"]["parameters"]
    assert deploy["resolve_image_repos"] is True and deploy["resolve_s3"] is True
    assert deploy["capabilities"] == "CAPABILITY_IAM", "no named IAM resources, so NAMED_IAM would be too broad"
    assert deploy["confirm_changeset"] is True


def test_template_is_clean_under_cfn_lint():
    """cfn-lint (the .venv has it) with warnings AND informational rules enabled: zero findings expected."""
    if importlib.util.find_spec("cfnlint") is None:
        pytest.skip("cfn-lint is not installed (uv pip install cfn-lint)")
    command = "import sys; from cfnlint.runner import main; sys.argv[0] = 'cfn-lint'; sys.exit(main())"
    result = subprocess.run(
        [sys.executable, "-c", command, "-c", "I", "-c", "W", "--non-zero-exit-code", "informational", "-t", str(TEMPLATE)],
        capture_output=True, text=True, check=False, timeout=240,
    )  # fmt: skip
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == ""


# ------------------------------------------------------------------------------------ SageMaker entry point

def load_train_entry():
    path = INFRA / "sagemaker" / "train_entry.py"
    spec = importlib.util.spec_from_file_location("jp_a_train_entry", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def train_entry():
    return load_train_entry()


def test_train_entry_defaults_follow_the_sagemaker_environment(train_entry):
    args = train_entry.parse_args([], {"SM_CHANNEL_TRAINING": "/opt/ml/input/data/training", "SM_MODEL_DIR": "/m"})
    assert args.train_dir == Path("/opt/ml/input/data/training") and args.model_dir == Path("/m")
    assert args.s3_bucket is None and args.s3_prefix == "a/" and args.region == "ap-south-1"
    # unknown hyper-parameters passed by SageMaker are tolerated
    assert train_entry.parse_args(["--seed", "7", "--unknown", "x"], {}).seed == 7


def test_train_entry_trains_with_jp_a_and_writes_the_artefacts(train_entry, tmp_path, capsys):
    train_csv, _ = write_synthetic_split(tmp_path / "channel")
    model_dir = tmp_path / "model"
    code = train_entry.main(["--train-dir", str(train_csv.parent), "--model-dir", str(model_dir)], environ={})
    assert code == 0
    assert (model_dir / "shortage_xgb_v1.json").is_file() and (model_dir / "model_meta.json").is_file()
    meta = json.loads((model_dir / "model_meta.json").read_text(encoding="utf-8"))
    assert meta["is_simulation"] is True and meta["model_version"] == "xgb-sa-v1"
    summary = json.loads(capsys.readouterr().out)
    assert summary["uploaded"] == [] and "metrics" in summary


def test_train_entry_copies_the_artefacts_to_s3_models_prefix(train_entry, tmp_path, capsys):
    train_csv, _ = write_synthetic_split(tmp_path / "channel")
    fake = FakeS3Client("jalpriority-a-test")
    code = train_entry.main(
        ["--train-dir", str(train_csv.parent), "--model-dir", str(tmp_path / "model"), "--s3-bucket", "jalpriority-a-test"],
        environ={},
        s3_client=fake,
    )
    assert code == 0
    assert sorted(fake.objects) == ["a/models/model_meta.json", "a/models/shortage_xgb_v1.json"]
    assert fake.objects["a/models/model_meta.json"] == (tmp_path / "model" / "model_meta.json").read_bytes()
    capsys.readouterr()


def test_train_entry_survives_an_s3_failure_and_reports_training_errors(train_entry, tmp_path, capsys):
    train_csv, _ = write_synthetic_split(tmp_path / "channel")
    fake = FakeS3Client("jalpriority-a-test")
    fake.fail_with["put_object"] = client_error("AccessDenied", "PutObject", 403)
    args = ["--train-dir", str(train_csv.parent), "--model-dir", str(tmp_path / "model"), "--s3-bucket", "jalpriority-a-test"]
    assert train_entry.main(args, environ={}, s3_client=fake) == 0  # the artefacts are in model_dir regardless
    assert "warning" in capsys.readouterr().err

    assert train_entry.main(["--train-dir", str(tmp_path / "missing"), "--model-dir", str(tmp_path / "m2")], environ={}) == 1
    assert "training failed" in capsys.readouterr().err
