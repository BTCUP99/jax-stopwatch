# 贡献指南

欢迎 Issue 和 PR，中文或英文都可以。

## 开发

```sh
git clone --recursive https://github.com/BTCUP99/jax-stopwatch.git
cd jax-stopwatch
scripts/setup.sh
.venv/bin/python -m unittest discover -s tests -v
```

- Mac 端代码在 `src/`，改动后运行测试；新增逻辑请配上测试。
- 固件改动请提到 [BTCUP99/stackchan-pet-fw](https://github.com/BTCUP99/stackchan-pet-fw) 的 `mac-bridge` 分支，再在本仓库更新子模块指针。通用的改进也可以考虑直接回馈上游 [Corvelis/stackchan-pet-fw](https://github.com/Corvelis/stackchan-pet-fw)。
- 涉及设备协议的改动，请同步更新 [docs/architecture.md](docs/architecture.md)，并用 `device.info` 的 `capabilities` 做兼容判断，让旧固件继续可用。
- 新形象按 [docs/avatars.md](docs/avatars.md) 交付，并说明来源。不接受现有动漫、游戏、品牌角色的复刻。

## 安全

- 不要提交 `runtime/`、证书、密钥、Webhook 地址、日志或个人路径；`.gitignore` 已排除常见位置，提交前请再检查一遍。
- 发现安全问题请通过 GitHub 的 Private vulnerability reporting 私下报告，不要公开开 Issue。
