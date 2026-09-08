# g1_api Docker 部署

镜像基于 `python:3.8-slim`，默认 **mock 模式**、所有 `G1_API_SAFETY_ALLOW_*`
安全开关保持内置默认 **false**（写类请求一律 `409 SAFETY_INTERLOCK`）。
容器内通过 `G1_API_SERVER_HOST=0.0.0.0` 覆盖默认的 127.0.0.1 监听地址。

## 构建

在**项目根目录**（`g1_api/`）执行：

```bash
docker build -f deploy/docker/Dockerfile -t g1-api:dev .
```

## 运行

宿主机 1448 常被本机开发实例占用，映射到 1449：

```bash
docker run -d --name g1-api -p 1449:1448 g1-api:dev

# 验证
curl http://127.0.0.1:1449/healthz                          # 应返回 mode=mock
curl http://127.0.0.1:1449/api/core/system/v1/robot/info
```

或用 compose（在本目录执行）：

```bash
docker compose up -d
docker compose down
```

## 打开安全开关

默认镜像里**没有**烤死任何 ALLOW=true，需要在运行时按需注入环境变量
（也可解开 `compose.yaml` 里 environment 段的注释示例）：

```bash
docker run -d --name g1-api -p 1449:1448 \
  -e G1_API_SAFETY_ALLOW_MOTION=true \
  -e G1_API_SAFETY_ALLOW_NAVIGATION=true \
  g1-api:dev
```

可用开关：`G1_API_SAFETY_ALLOW_MOTION` / `_NAVIGATION` / `_MAP_WRITE` /
`_ARM` / `_HAND` / `_VOICE` / `_TOUR`。真机模式还需
`G1_API_MODE=real` 和 `G1_API_MODE_NETWORK_INTERFACE=<网卡名>`（无默认值）。
真机相关操作请先读 `docs/DEPLOY_PC2.md`。

## 多架构构建（arm64，给 Jetson 用）

需要 buildx 与 qemu binfmt（`ls /proc/sys/fs/binfmt_misc/ | grep qemu` 有
`qemu-aarch64` 即可；没有的话先 `docker run --privileged --rm tonistiigi/binfmt --install arm64`，需要 root）。

```bash
# 只构建 arm64 并加载到本地（在项目根目录执行）
docker build --platform linux/arm64 -f deploy/docker/Dockerfile -t g1-api:dev-arm64 --load .

# 一次构建两种架构（--push 推 registry；本地 docker 驱动不能同时 --load 两个平台）
docker buildx build --platform linux/amd64,linux/arm64 \
  -f deploy/docker/Dockerfile -t <registry>/g1-api:dev --push .
```

在 Jetson 上也可以直接本机构建（原生 arm64，无需 qemu）。
