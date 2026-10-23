# 安装与刷机

## 1. Mac 端

需要 Apple silicon Mac（语音识别跑在 MLX 上）和 [uv](https://docs.astral.sh/uv/)。

```sh
git clone --recursive https://github.com/BTCUP99/jax-stopwatch.git
cd jax-stopwatch
scripts/setup.sh
```

`setup.sh` 会：

1. 按 `uv.lock` 建立 Python 3.12 环境（`.venv/`）；
2. 下载 [mlx-community/whisper-small-mlx](https://huggingface.co/mlx-community/whisper-small-mlx)（固定版本，约 480MB）和 Silero VAD 到 `models/`，逐个校验 SHA-256；
3. 建立 `runtime/`（私密状态，权限 700）和 `workspace/`（Codex 工作区）。

启动与停止：

```sh
scripts/start.command      # 或在 Finder 里双击
scripts/stop.command
.venv/bin/python src/manage.py status
```

控制台在 <http://127.0.0.1:18765>，关闭网页不会停止后台。

### 开机自启动

```sh
.venv/bin/python src/manage.py autostart on    # 关闭：autostart off
```

会写入登录项 `~/Library/LaunchAgents/io.github.jax-stopwatch.bridge.plist`：登录后启动，异常退出 30 秒后重启；`manage.py stop` 的正常停止不会被拉起。如果你的终端设置了 `HTTP(S)_PROXY`，开启时会一并写入登录项。

## 2. 备份原厂固件

刷机前强烈建议备份整片 Flash，之后随时可以恢复出厂状态。插上手表，关闭其他占用串口的软件：

```sh
ls /dev/cu.usbmodem*      # 找到手表的串口
uv run --with esptool esptool --chip esp32s3 --port /dev/cu.usbmodemXXXX read-flash 0 ALL stopwatch-factory.bin
shasum -a 256 stopwatch-factory.bin > stopwatch-factory.bin.sha256
```

把这两个文件存到安全的地方（不要提交到仓库）。恢复时：

```sh
uv run --with esptool esptool --chip esp32s3 --port /dev/cu.usbmodemXXXX write-flash 0x0 stopwatch-factory.bin
```

恢复会覆盖设备上的一切，包括无线配对和上传的形象。

## 3. 刷入 Jax 固件

**第一次**（手表还是原厂固件，或想完全重装）：

```sh
scripts/flash.sh --first-install
```

脚本先写入上游 [stackchan-pet-fw v0.4.1](https://github.com/Corvelis/stackchan-pet-fw/releases/tag/v0.4.1) 的 StopWatch factory 镜像（引导程序、分区表和内置脸部图片，校验 SHA-256），再把 Jax 应用写到上面。这一步会**清空整片 Flash**，请先完成上一节的备份。

**之后升级**：

```sh
scripts/flash.sh
```

只写应用分区 `0x10000`，设备里的 Wi-Fi 配对和上传的形象都保留。两种方式都会从 [Releases](https://github.com/BTCUP99/jax-stopwatch/releases) 下载 `jax-stopwatch-0.3.0.bin` 并校验，自动找到唯一一台 StopWatch，临时停止后台、刷完再启动。

> 只适用于 M5Stack **StopWatch**（ESP32-S3，8MB 分区表）。不要把其他板型的镜像刷到这台设备上。

刷完后设备重启，几秒内控制台显示“设备已连接”，首页底部出现浅蓝插头图标。

### 自己编译固件

固件在子模块 `firmware/`（[BTCUP99/stackchan-pet-fw](https://github.com/BTCUP99/stackchan-pet-fw) 的 `mac-bridge` 分支），用 PlatformIO 编译：

```sh
cd firmware
uv run --with platformio pio run -e m5stack-stopwatch-release
cd ..
scripts/flash.sh firmware/.pio/build/m5stack-stopwatch-release/firmware.bin
```

首页、好感度等开关在 `firmware/src/config.h`：`STACKCHAN_MAC_BRIDGE_HOME`、`STACKCHAN_AFFECTION_ENABLED`。

## 4. 更新

```sh
git pull --recurse-submodules
uv sync --locked
scripts/stop.command && scripts/start.command
```

固件有新版本时 `scripts/flash.sh` 的版本号会跟着更新，重新运行即可。

## 5. 卸载

```sh
.venv/bin/python src/manage.py autostart off
scripts/stop.command
```

然后删除仓库目录。若使用了 Tailscale Funnel：`tailscale funnel --https=8443 off`。
