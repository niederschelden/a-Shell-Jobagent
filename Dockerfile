FROM python:3.12-slim

COPY release/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

WORKDIR /arbeit
ENTRYPOINT ["python", "-m", "a_shell_agent"]
