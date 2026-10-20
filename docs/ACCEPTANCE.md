# 发布验收 · 2026-10-20

## 已验证

- Windows / Python 3.11：`python -m pytest -q`，35 项通过，包括区间租金、年/日租、输入转义与配对认证。
- 真实 WebSocket 测试覆盖错误配对拒绝、扩展握手与请求往返；全新虚拟环境按 uv.lock 安装后通过。
- Skill frontmatter 校验通过；离线 demo 生成报告。
- ZIP 仅打包白名单文件，并记录各文件 SHA-256 与源码版本。
- ZIP 解压到仓库外运行 demo 通过，逐文件 SHA-256 核对通过。
- wheel 安装到无系统包的新虚拟环境，从仓库外生成 demo 通过。

## 待完成

真实登录态采集、扩展权限与页面改版兼容、平台购买安装。

Windows/Linux、Python 3.11/3.12 的 4 项 CI 任务在
[5ef2302 的运行](https://github.com/KardeniaPoyu/xhs-rental-hunter/actions/runs/39461201643)全部通过。
平台上线需真实审核和购买链路通过；当前没有可申报的付费用户数据。
