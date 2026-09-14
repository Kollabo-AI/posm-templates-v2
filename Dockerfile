FROM public.ecr.aws/lambda/python:3.12@sha256:beae84cc45e41c80aa709a11f702b452a8aea4141700cdbab47ac4a6c181113f
RUN dnf --releasever 2023.12.20260831 install -y fontconfig libstdc++ && dnf clean all
COPY requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt
COPY app ${LAMBDA_TASK_ROOT}/app
COPY renderer ${LAMBDA_TASK_ROOT}/renderer
COPY render_service.py worker.py lambda_handler.py templates_api.py ${LAMBDA_TASK_ROOT}/
ENV POSM_LOG_DIR=/tmp/posm-logs
RUN python -c "from app.native import Bundle; Bundle.configured().verify()"
CMD ["lambda_handler.handler"]
