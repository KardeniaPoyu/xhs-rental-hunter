# Rental Hunter · 租房候选对比

把分散的租房帖子整理成带原文依据的报告：哪些符合预算，哪些条件冲突，
还缺什么信息，下一步怎么问。

**1.1.0 候选版本**：本地报告可用；Chrome 采集是实验功能，SkillPay
安装、付费和审核尚待平台实测。

## 先体验

需要 Python 3.11+：

```bash
python -m venv .venv
# Windows：.venv\Scripts\activate
# macOS / Linux：source .venv/bin/activate
python -m pip install .
python -m rental_hunter demo --output demo-output
```

打开 `demo-output/report.html`。示例为原创合成内容，不代表真实房源。
核心报告仅使用 Python 标准库，不需要小红书账号、模型或 API 密钥。

## 比较自己的候选

将有权使用的帖子保存为 `listings.json`：

```json
[{"id":"example-1","title":"整租一居室","desc":"月租3800元，押一付一，周末可约看房。"}]
```

```bash
python -m rental_hunter report --input listings.json --min-budget 2000 --max-budget 5000 --housing entire --output report-v1
```

| 交付 | 用途 |
|---|---|
| report.html | 原文依据、条件差异、待核实事项 |
| report.json | 结构化结果 |
| questions.md | 询问草稿，尚未发送 |

出租方式支持 `entire`（整租）、`shared`（合租）、`any`（不限）。
月租需有明确标签或每月单位；多个报价标记待确认。最多 500 条、5 MB，
自动去重，不覆盖现有报告目录。
默认预算为 0–5000 元、整租；报告与浏览器流水线共用同一默认值。
只有完整记录相同才去重，同 ID 但内容不同的记录会保留。

## 边界

- 比较输入文字，不靠职业、表情、文案风格推断真假。
- 不核实房东身份、产权、室内空气、房屋质量或实时可租状态。
- 没有官方小红书 API，页面变化或访问限制可能影响采集。
- 不代付押金或租金，不自动联系发布者；旧 comment 命令现在只写草稿。
- 原文可能含个人信息，分享前需检查。报告不会输出链接认证查询参数。

## 可选 Chrome 采集

安装 `python -m pip install ".[browser]"`，并准备本地登录态、扩展和配对码，
见 [浏览器接入](references/browser.md)。
本版 Bridge 使用双向配对认证与签名消息，超时请求按原编号恢复。
升级时需同时更新服务端、CLI 和扩展，详见上述浏览器接入说明。
旧仓库通用发布、点赞等工具不属于本次商品包。

## 开发

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python tools/build_skill.py
```

白名单商品包排除历史实拍图、个人房源、缓存、Cookie、日志与发布工具。
参见 [商品说明](docs/SKILLPAY.md)、[验收记录](docs/ACCEPTANCE.md)。
源码按 [MIT](LICENSE) 开源；付费商品不能改变已有源码授权。
