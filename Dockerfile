FROM rust:1.97.0-alpine3.23@sha256:ca0daf101eef0c8cd1e49dfc137154a220efb8c458c85a3eacacc7dfd5d9e04c AS workload-credentials-provider
WORKDIR /build/aws-workload-credentials-provider
ADD --checksum=sha256:7df85c24d0634b55c0839811db1fb6e8ddd91121611510dfa1e1331a47083812 https://codeload.github.com/aws/aws-workload-credentials-provider/tar.gz/f76ef6f80ae1614fc29856967bbd1a431791422b /tmp/provider.tar.gz
RUN tar -xzf /tmp/provider.tar.gz --strip-components=1 \
    && CARGO_BUILD_JOBS=1 cargo build --locked --release --package aws_workload_credentials_provider \
    && test "$(./target/release/aws-workload-credentials-provider --version)" = "aws-workload-credentials-provider 3.1.1"

FROM public.ecr.aws/lambda/python:3.12@sha256:beae84cc45e41c80aa709a11f702b452a8aea4141700cdbab47ac4a6c181113f
# ALAS2023-2026-2136 fixes the OpenSSL HIGH findings in the pinned base image.
RUN dnf --releasever 2023.12.20260914 install -y fontconfig libstdc++ \
        openssl-fips-provider-latest-3.5.8-1.amzn2023.0.1 \
        openssl-snapsafe-libs-3.5.8-1.amzn2023.0.1 \
    && dnf clean all
COPY requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt
COPY app ${LAMBDA_TASK_ROOT}/app
COPY renderer/linux-x86_64 ${LAMBDA_TASK_ROOT}/renderer/linux-x86_64
COPY render_service.py worker.py lambda_handler.py templates_api.py asm-exec lambda_entrypoint.py ${LAMBDA_TASK_ROOT}/
COPY --from=workload-credentials-provider /build/aws-workload-credentials-provider/target/release/aws-workload-credentials-provider /opt/bin/aws-workload-credentials-provider
COPY lambda_secrets_extension.py /opt/extensions/secrets-manager-provider-extension
RUN chmod 0555 ${LAMBDA_TASK_ROOT}/asm-exec /opt/bin/aws-workload-credentials-provider /opt/extensions/secrets-manager-provider-extension ${LAMBDA_TASK_ROOT}/renderer/linux-x86_64/posm-renderer \
    && test -x /var/lang/bin/python3
ENV POSM_LOG_DIR=/tmp/posm-logs
RUN python -c "from app.native import Bundle; from app.template_contract import NATIVE_TEMPLATES; result = Bundle.configured().invoke({'protocol_version': 1, 'request_id': 'container-build', 'method': 'describe'}); assert result['devtools'] is False; assert {t['id'] for t in result['templates']} == set(NATIVE_TEMPLATES)"
ENTRYPOINT ["python3", "/var/task/lambda_entrypoint.py"]
CMD ["lambda_handler.handler"]
