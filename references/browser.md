# 可选浏览器采集（实验功能）

优先用用户提供的帖子验证报告。网页采集不是官方 API，可能随页面变化失效。
登录、验证码由用户完成；受限时停止，改用文本，不绕过访问限制。

## 配对

1. 在解压目录运行 `python -m pip install ".[browser]"`。
2. 用户在 Chrome 扩展页以“加载已解压扩展”选择包内 `extension`。
3. 生成配对码：`python -c "import secrets; print(secrets.token_urlsafe(32))"`。
   将同一码填入扩展弹窗，并设置环境变量 `XHS_BRIDGE_TOKEN`。
4. 在设置了变量的终端运行 `python scripts/bridge_server.py`。
   仅监听 `127.0.0.1:9333`。采集终端也需相同变量。

PowerShell：`$env:XHS_BRIDGE_TOKEN = '<本机生成的配对码>'`；
macOS/Linux：`export XHS_BRIDGE_TOKEN='<本机生成的配对码>'`。
不要使用占位文本当密钥，不上传配对码或提交到 Git。

本版采用协议 v2，服务端、CLI 和扩展必须一起更新，并在 Chrome 中重新加载扩展。
双方通过 HMAC 挑战验证身份，配对码本身不通过 WebSocket 发送；
后续消息也校验签名、方向和序号。此协议提供认证与完整性校验，不加密消息内容，
因此仍只用于本机回环连接。

## 超时与重试

超时不代表浏览器操作已经停止。同一个未完成请求重试时沿用原编号，
只等待或读取原结果；关闭后重新运行 CLI 也会保留这个编号。
编号和请求摘要保存在 Windows 的 `%LOCALAPPDATA%/xhs-bridge/requests.sqlite3`，
其他系统的 `~/.cache/xhs-bridge/requests.sqlite3`；不保存配对码或命令参数。
可用 `XHS_REQUEST_STATE` 指定状态文件位置。

若提示“结果未知”或“请求号不属于当前服务”，先检查浏览器中的实际结果，
不要通过删除状态文件反复重发。Bridge 重启后不会自动重放旧请求。
当前服务最多记录 128 个请求，扩展最多记录 256 个；达到上限时拒绝新操作，
核实没有未完成操作后再重启服务并重新加载扩展。单条消息上限为 1 MiB，
大型截图或日志应缩小后再取。

## 分步采集

```bash
python scripts/xhs_rental_pipeline.py search --keywords "所在城市 整租" --work-dir /absolute/work
python scripts/xhs_rental_pipeline.py filter --raw-file /absolute/work/raw_feeds.json --work-dir /absolute/work
python scripts/xhs_rental_pipeline.py inspect --filtered-file /absolute/work/filtered_feeds.json --limit 10 --work-dir /absolute/work
python -m rental_hunter report --input /absolute/work/detailed_posts.json --max-budget 5000 --housing entire --output /absolute/new-report
```

替换真实城市、预算和路径，先用少量候选验证。查询 token 只供本地适配器使用。
新 HTML 报告不自动下载图片；旧 PNG 海报依赖 Windows 字体。

## 权限与验证

扩展使用小红书页面、标签页、脚本及调试权限。配对认证不等于已完成整个
旧扩展的安全审计。已验证双向认证、消息签名、请求往返、超时去重和
Python/JavaScript 协议互通；真实 Chrome 登录采集仍待现场验证。
不要将本机配对服务暴露到公网，或将浏览器登录态拿到云上运行。
