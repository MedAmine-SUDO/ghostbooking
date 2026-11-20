# Ghostbooking: the scheduler + the Keep/Release link endpoint.
#   docker run -v ./ghostbooking:/config -v "/path/to/shared folder":/data -p 8080:8080 ghostbooking
# /config holds config.yaml; state, the audit log and backups are written next to it.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.0 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --extra google --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --extra google --no-editable

FROM python:3.12-slim
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
RUN useradd --uid 1000 --create-home ghostbooking
USER ghostbooking
WORKDIR /config
VOLUME ["/config", "/data"]
EXPOSE 8080
ENTRYPOINT ["ghostbooking"]
CMD ["run", "-c", "/config/config.yaml"]
