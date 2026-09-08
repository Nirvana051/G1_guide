# g1_api — Slamtec 模式机器人 API（默认 mock 后端）
# 构建（在项目根目录执行）：
#   docker build -f deploy/docker/Dockerfile -t g1-api:dev .
FROM python:3.8-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# --- 依赖层：先只拷依赖清单，命中层缓存 ---------------------------------
# 依赖列表以 pyproject.toml 的 [project].dependencies 为唯一事实来源，
# 用 tomli 解析后安装，避免在 Dockerfile 里手抄一份造成漂移。
COPY pyproject.toml ./
RUN pip install tomli \
    && python -c "import tomli; deps = tomli.load(open('pyproject.toml','rb'))['project']['dependencies']; open('requirements.txt','w').write(chr(10).join(deps))" \
    && pip install -r requirements.txt \
    && rm requirements.txt

# --- 代码层 --------------------------------------------------------------
COPY g1_api ./g1_api
COPY config ./config

# 非 root 运行；map_storage_dir 默认在 ~/.g1_api/maps，需要可写的 HOME。
# COPY 会保留构建上下文里的文件权限（可能是 600），统一放开为全员可读，
# 否则非 root 用户 import 不了 /app/g1_api。
RUN useradd --create-home --uid 10001 g1api \
    && chmod -R a+rX /app/g1_api /app/config
USER g1api

# 默认安全姿态：mock 模式，所有 G1_API_SAFETY_ALLOW_* 保持内置默认 false。
# 容器内必须监听 0.0.0.0（G1_API_SERVER_HOST 覆盖 yaml 里的 127.0.0.1）。
ENV G1_API_CONFIG=/app/config/default.yaml \
    G1_API_SERVER_HOST=0.0.0.0 \
    G1_API_MODE=mock

EXPOSE 1448

# slim 镜像没有 curl，用 python 标准库探活
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:1448/healthz', timeout=4).getcode() == 200 else 1)"

CMD ["python", "-m", "g1_api"]
