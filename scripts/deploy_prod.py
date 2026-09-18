#!/usr/bin/env python3
"""Stage verified production images; the live alias is the sole traffic switch."""
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import time

STACK = "posm-singapore-prod-runtime"
FUNCTION = "posm-templates-lambda-prod-sg"
REGION = "ap-southeast-1"
ACCOUNT = "137307166614"


def aws(*args):
    result = subprocess.run(
        ["aws", *args, "--region", REGION, "--output", "json"],
        capture_output=True, text=True,
    )
    if result.returncode:
        raise RuntimeError(f"AWS {' '.join(args[:2])} failed: {result.stderr.strip()}")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def write_json(name, value):
    Path(name).write_text(json.dumps(value), encoding="utf-8")
    return "file://" + name


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def parameters_for_image(parameters, image):
    values = {p["ParameterKey"]: p["ParameterValue"] for p in parameters}
    require(values.get("ProdPosterAliasEnabled") == "true", "Production must route through live before staging")
    require("ProdPosterArchitecture" in values, "Production architecture parameter is missing")
    return [
        {"ParameterKey": key, "ParameterValue": image if key == "ProdPosterImageUri" else "x86_64"}
        if key in {"ProdPosterImageUri", "ProdPosterArchitecture"}
        else {"ParameterKey": key, "UsePreviousValue": True}
        for key in values
    ]


def should_activate(not_before, now, live_architecture):
    # A failed/missed initial schedule must leave legacy live, even after the date.
    return now >= dt.datetime.fromisoformat(not_before.replace("Z", "+00:00")) and live_architecture == "x86_64"


def verify_mapping(mapping, function_arn):
    require(mapping["State"] == "Enabled", "Production queue mapping is not enabled")
    require(mapping["FunctionArn"] == function_arn + ":live", "Production queue does not invoke live")
    require(mapping["BatchSize"] == 1 and mapping["ScalingConfig"]["MaximumConcurrency"] == 5, "Unexpected queue capacity")
    require(mapping["FunctionResponseTypes"] == ["ReportBatchItemFailures"], "Partial batch responses are required")


def probe(function, qualifier):
    metadata = aws("lambda", "invoke", "--function-name", function, "--qualifier", qualifier,
                   "--cli-binary-format", "raw-in-base64-out", "--payload", '{"Records":[]}', "response.json")
    require("FunctionError" not in metadata, "Lambda smoke invocation failed: " + Path("response.json").read_text())
    require(json.loads(Path("response.json").read_text()) == {"batchItemFailures": []}, "Unexpected smoke response")


def wait_for_scan(repository, digest, attempts=60):
    for _ in range(attempts):
        try:
            scan = aws("ecr", "describe-image-scan-findings", "--repository-name", repository, "--image-id", "imageDigest=" + digest)
        except RuntimeError as error:
            # Scan-on-push registration is eventually consistent. Do not suppress
            # authorization errors or weaken the severity/completion gates.
            if "ScanNotFoundException" not in str(error):
                raise
            time.sleep(10)
            continue
        state = scan["imageScanStatus"]["status"]
        if state == "COMPLETE":
            severity = scan.get("imageScanFindings", {}).get("findingSeverityCounts", {})
            require(not severity.get("HIGH", 0) and not severity.get("CRITICAL", 0), "ECR severity gate failed")
            return severity
        require(state in {"IN_PROGRESS", "PENDING"}, "ECR scan failed: " + state)
        time.sleep(10)
    raise RuntimeError("ECR scan timed out")


def main():
    require(os.environ.get("GITHUB_REF") == "refs/heads/prod", "Only prod can stage production")
    require(os.environ.get("AWS_REGION") == REGION, "Wrong AWS region")
    commit = os.environ["GITHUB_SHA"]
    require(re.fullmatch(r"[a-f0-9]{40}", commit), "Expected immutable commit SHA")
    require(aws("sts", "get-caller-identity")["Account"] == ACCOUNT, "Wrong AWS account")
    stack = aws("cloudformation", "describe-stacks", "--stack-name", STACK)["Stacks"][0]
    require(stack["StackStatus"] in {"CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE"}, "Stack is not ready")
    outputs = {p["OutputKey"]: p["OutputValue"] for p in stack["Outputs"]}
    parameters = {p["ParameterKey"]: p["ParameterValue"] for p in stack["Parameters"]}
    function = outputs["ProdPosterFunctionArn"]
    repository = outputs["ProdPosterRepositoryUri"]
    require(function == f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}", "Unexpected function")
    require(repository == f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/{FUNCTION}", "Unexpected repository")
    mapping_id = outputs["ProdPosterEventSourceMappingId"]
    verify_mapping(aws("lambda", "get-event-source-mapping", "--uuid", mapping_id), function)
    previous = aws("lambda", "get-alias", "--function-name", function, "--name", "live")
    require(not previous.get("RoutingConfig", {}).get("AdditionalVersionWeights"), "Weighted production routing is unsupported")
    write_json("previous-live.json", previous)
    old_version = aws("lambda", "get-function-configuration", "--function-name", function, "--qualifier", previous["FunctionVersion"])
    # Reject an unbootstrapped stack before publishing anything.
    parameters_for_image(stack["Parameters"], repository + "@sha256:" + "0" * 64)

    images = aws("ecr", "list-images", "--repository-name", FUNCTION)["imageIds"]
    if not any(i.get("imageTag") == commit for i in images):
        subprocess.run(["docker", "load", "--input", "lambda-image.tar.gz"], check=True)
        subprocess.run(["docker", "tag", "posm-templates-v2:verified", repository + ":" + commit], check=True)
        subprocess.run(["docker", "push", repository + ":" + commit], check=True)
    image = aws("ecr", "describe-images", "--repository-name", FUNCTION, "--image-ids", "imageTag=" + commit)["imageDetails"][0]
    digest = image["imageDigest"]
    require(re.fullmatch(r"sha256:[a-f0-9]{64}", digest), "Invalid image digest")
    image_uri = repository + "@" + digest
    severity = wait_for_scan(FUNCTION, digest)

    if parameters["ProdPosterImageUri"] != image_uri or parameters["ProdPosterArchitecture"] != "x86_64":
        aws("cloudformation", "update-stack", "--stack-name", STACK, "--use-previous-template",
            "--parameters", write_json("update-parameters.json", parameters_for_image(stack["Parameters"], image_uri)),
            "--capabilities", "CAPABILITY_NAMED_IAM")
        aws("cloudformation", "wait", "stack-update-complete", "--stack-name", STACK)
    aws("lambda", "wait", "function-updated-v2", "--function-name", function)
    current = aws("lambda", "get-function", "--function-name", function)
    require(current["Code"]["ImageUri"] == image_uri, "Staged image does not match")
    require(current["Configuration"]["Architectures"] == ["x86_64"], "Staged architecture does not match")
    # Immutable version smoke tests exercise the production extension and secrets
    # without consuming a queue item or changing a customer's job/wallet.
    description = "templates-v2 " + commit
    versions = aws("lambda", "list-versions-by-function", "--function-name", function)["Versions"]
    matches = [v for v in versions if v["Version"] != "$LATEST" and v.get("Description") == description
               and v["CodeSha256"] == current["Configuration"]["CodeSha256"]]
    if matches:
        version = matches[-1]["Version"]
    else:
        version = aws("lambda", "publish-version", "--function-name", function,
                      "--revision-id", current["Configuration"]["RevisionId"],
                      "--description", description)["Version"]
    aws("lambda", "wait", "function-active-v2", "--function-name", function, "--qualifier", version)
    probe(function, version)
    verify_mapping(aws("lambda", "get-event-source-mapping", "--uuid", mapping_id), function)
    live = aws("lambda", "get-alias", "--function-name", function, "--name", "live")
    require(live["RevisionId"] == previous["RevisionId"], "Live routing changed during deployment; leave it untouched")
    activate = should_activate(parameters["ProdPosterActivationNotBefore"], dt.datetime.now(dt.timezone.utc), old_version["Architectures"][0])
    if activate:
        updated = aws("lambda", "update-alias", "--function-name", function, "--name", "live",
                      "--function-version", version, "--revision-id", previous["RevisionId"])
        try:
            probe(function, "live")
        except Exception:
            aws("lambda", "update-alias", "--function-name", function, "--name", "live",
                "--function-version", previous["FunctionVersion"], "--revision-id", updated["RevisionId"])
            raise
    release = {"commit": commit, "image": image_uri, "function": function, "version": version,
               "previous_version": previous["FunctionVersion"], "previous_revision": previous["RevisionId"],
               "activated": activate, "scan_counts": severity}
    write_json("production-release.json", release)
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
        summary.write("## Production templates v2\n\n```json\n" + json.dumps(release, indent=2) + "\n```\n")
        if not activate:
            summary.write("Candidate staged and verified. Live alias remains unchanged; initial activation belongs to the scheduled cutover.\n")
    print(json.dumps(release))


if __name__ == "__main__":
    main()
