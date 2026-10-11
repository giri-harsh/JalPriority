# JalPriority AI - Member A API as an AWS Lambda container image (FastAPI + Mangum).
# Build context is the member-a directory (SAM: DockerContext "..", Dockerfile "infra/lambda.Dockerfile").
FROM public.ecr.aws/lambda/python:3.12

# libgomp: OpenMP runtime that the xgboost wheel links against.
RUN dnf install -y libgomp && dnf clean all

COPY backend/requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY backend/jp_a ${LAMBDA_TASK_ROOT}/jp_a
COPY data ${LAMBDA_TASK_ROOT}/data
COPY models ${LAMBDA_TASK_ROOT}/models
RUN mkdir -p ${LAMBDA_TASK_ROOT}/data/derived

# The image filesystem is read-only; jp_a.lambda_handler copies the data directory to /tmp when it must write.
ENV A_DATA_DIR=${LAMBDA_TASK_ROOT}/data \
    A_MODELS_DIR=${LAMBDA_TASK_ROOT}/models \
    SHARED_ENV=aws

CMD ["jp_a.lambda_handler.handler"]
