#!/usr/bin/env bash
set -euo pipefail

test "${GITHUB_REF}" = refs/heads/dev
test "${AWS_REGION}" = ap-southeast-1
test "${STACK_NAME}" = posm-singapore-data-audit
test "$(aws sts get-caller-identity --query Account --output text)" = 137307166614
outputs="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query 'Stacks[0].Outputs' --output json)"
value() { jq -er --arg key "$1" '.[] | select(.OutputKey == $key) | .OutputValue' <<<"$outputs"; }
repository_uri="$(value DevPosterRepositoryUri)"
repository_name="${repository_uri#*/}"
function_arn="$(value DevPosterFunctionArn)"
mapping_id="$(value DevPosterEventSourceMappingId)"
test "$repository_name" = posm-templates-lambda-dev
test "${function_arn##*:}" = posm-templates-lambda-dev
test "$(aws lambda get-event-source-mapping --uuid "$mapping_id" --query State --output text)" = Enabled

# Immutable commit tags allow safe retries without replacing an already published image.
tagged_uri="${repository_uri}:${GITHUB_SHA}"
if ! aws ecr describe-images --repository-name "$repository_name" --image-ids "imageTag=$GITHUB_SHA" >/dev/null 2>&1; then
  gunzip -c lambda-image.tar.gz | docker load
  docker tag posm-templates-v2:verified "$tagged_uri"
  docker push "$tagged_uri"
fi
digest="$(aws ecr describe-images --repository-name "$repository_name" --image-ids "imageTag=$GITHUB_SHA" --query 'imageDetails[0].imageDigest' --output text)"
[[ "$digest" =~ ^sha256:[a-f0-9]{64}$ ]]
image_uri="${repository_uri}@${digest}"
scan_status=
for _ in $(seq 1 60); do
  scan_status="$(aws ecr describe-image-scan-findings --repository-name "$repository_name" --image-id "imageDigest=$digest" --query 'imageScanStatus.status' --output text 2>/dev/null || true)"
  case "$scan_status" in
    COMPLETE) break ;;
    FAILED|UNSUPPORTED_IMAGE|FINDINGS_UNAVAILABLE|LIMIT_EXCEEDED) echo "ECR scan failed: $scan_status" >&2; exit 1 ;;
  esac
  sleep 10
done
test "$scan_status" = COMPLETE
severe="$(aws ecr describe-image-scan-findings --repository-name "$repository_name" --image-id "imageDigest=$digest" --query 'imageScanFindings.findingSeverityCounts.[CRITICAL,HIGH]' --output text)"
read -r critical high <<<"$severe"
if [ "${critical/None/0}" != 0 ] || [ "${high/None/0}" != 0 ]; then
  echo "ECR scan blocked deployment: ${critical/None/0} critical and ${high/None/0} high findings" >&2
  exit 1
fi

# Record a rollback target before CloudFormation changes code and architecture.
aws lambda get-function --function-name "$function_arn" --query '{Image:Code.ImageUri,Architecture:Configuration.Architectures[0]}' > rollback.json
deployment_changed=false
rollback_on_error() {
  local status=$?
  trap - ERR
  if [ "$deployment_changed" = true ]; then
    echo 'Dev verification failed; restoring the previous image and architecture' >&2
    jq --slurpfile previous rollback.json 'map(
      if . == "DevPosterImageUri" then {ParameterKey:.,ParameterValue:$previous[0].Image}
      elif . == "DevPosterArchitecture" then {ParameterKey:.,ParameterValue:$previous[0].Architecture}
      else {ParameterKey:.,UsePreviousValue:true} end
    )' parameter-keys.json > rollback-parameters.json
    if aws cloudformation update-stack --stack-name "$STACK_NAME" --use-previous-template --parameters file://rollback-parameters.json --capabilities CAPABILITY_NAMED_IAM; then
      aws cloudformation wait stack-update-complete --stack-name "$STACK_NAME" || true
    fi
  fi
  exit "$status"
}
trap rollback_on_error ERR
started=false
no_change=false
for _ in $(seq 1 30); do
  state="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query 'Stacks[0].StackStatus' --output text)"
  if [[ "$state" == *_IN_PROGRESS ]]; then sleep 10; continue; fi
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" --query 'Stacks[0].Parameters[].ParameterKey' --output json > parameter-keys.json
  jq -e 'index("DevPosterArchitecture") != null' parameter-keys.json >/dev/null
  jq --arg image "$image_uri" 'map(
    if . == "DevPosterImageUri" then {ParameterKey:.,ParameterValue:$image}
    elif . == "DevPosterArchitecture" then {ParameterKey:.,ParameterValue:"x86_64"}
    else {ParameterKey:.,UsePreviousValue:true} end
  )' parameter-keys.json > update-parameters.json
  if update_output="$(aws cloudformation update-stack --stack-name "$STACK_NAME" --use-previous-template --parameters file://update-parameters.json --capabilities CAPABILITY_NAMED_IAM 2>&1)"; then
    started=true; deployment_changed=true; break
  elif [[ "$update_output" == *"No updates are to be performed"* ]]; then
    no_change=true; break
  elif [[ "$update_output" == *"_IN_PROGRESS"* ]]; then
    sleep 10
  else
    printf '%s\n' "$update_output" >&2; exit 1
  fi
done
if [ "$started" = true ]; then
  aws cloudformation wait stack-update-complete --stack-name "$STACK_NAME"
elif [ "$no_change" != true ]; then
  echo 'Timed out waiting for the dev stack' >&2; exit 1
fi
aws lambda wait function-updated-v2 --function-name "$function_arn"
test "$(aws lambda get-function --function-name "$function_arn" --query Code.ImageUri --output text)" = "$image_uri"
test "$(aws lambda get-function-configuration --function-name "$function_arn" --query 'Architectures[0]' --output text)" = x86_64
test "$(aws lambda get-event-source-mapping --uuid "$mapping_id" --query State --output text)" = Enabled
aws lambda invoke --function-name "$function_arn" --cli-binary-format raw-in-base64-out --payload '{"Records":[]}' response.json > invoke-metadata.json
if jq -e 'has("FunctionError")' invoke-metadata.json >/dev/null; then
  cat invoke-metadata.json response.json >&2
fi
jq -e 'has("FunctionError") | not' invoke-metadata.json >/dev/null
jq -e '. == {batchItemFailures:[]}' response.json >/dev/null
{
  echo '## Templates v2 dev deployment'
  echo "- Commit: $GITHUB_SHA"
  echo "- Image: $image_uri"
  echo "- Lambda: $function_arn (x86_64)"
  echo '- SQS mapping: Enabled; empty-record invocation: passed'
  echo "- Previous deployment: $(jq -c . rollback.json)"
} >> "$GITHUB_STEP_SUMMARY"
