# Sourcery review 修订对照

本表对应 PR #2 的本轮 15 条行内意见，记录实现与回归证据；不代表机器人已经重新批准。

| 原意见 | 修改结果 | 回归覆盖 |
|---|---|---|
| [4327775018](https://github.com/KardeniaPoyu/xhs-rental-hunter/pull/2#discussion_r4327775018) | 双方先验证 HMAC 身份证明；从不发送配对码，后续消息签名并校验序号 | 伪造服务、篡改/重放与 Python/JS 互通 |
| [4327775048](https://github.com/KardeniaPoyu/xhs-rental-hunter/pull/2#discussion_r4327775048) | HTTP 握手前过滤 Origin，限制未认证连接并缩短超时 | 恶意 Origin、空闲连接上限 |
| [4327775062](https://github.com/KardeniaPoyu/xhs-rental-hunter/pull/2#discussion_r4327775062) | 先独占注册扩展再发 ready；仅连接所有者可清理 | 重复扩展连接、立即状态查询 |
| [4327775075](https://github.com/KardeniaPoyu/xhs-rental-hunter/pull/2#discussion_r4327775075) | 分享分析 JSON 不复制 xsec_token | 分析文件无来源查询 token |
| [4327775087](https://github.com/KardeniaPoyu/xhs-rental-hunter/pull/2#discussion_r4327775087) | 捕获发送中断，返回并缓存结构化 unknown | 发送时 ConnectionClosed |
| [4327775099](https://github.com/KardeniaPoyu/xhs-rental-hunter/pull/2#discussion_r4327775099) | 持久化稳定请求编号；超时保持运行，重试仅读取原操作；重启服务拒绝重放旧编号 | 晚到结果、重复请求、CLI 重启、旧服务编号 |
| [4327775103](https://github.com/KardeniaPoyu/xhs-rental-hunter/pull/2#discussion_r4327775103) | 排除金额前后的年租标记 | 年租金36000元、租金36000元一年 |
| [4327775115](https://github.com/KardeniaPoyu/xhs-rental-hunter/pull/2#discussion_r4327775115) | 解析完整千分位金额与万/千/k 单位 | 5,000元、1.2万元及范围 |
| [4327775127](https://github.com/KardeniaPoyu/xhs-rental-hunter/pull/2#discussion_r4327775127) | 关联同分句费用标签，允许中间说明文字 | 月租3800元，服务费另收300元/月 |
| [4327775139](https://github.com/KardeniaPoyu/xhs-rental-hunter/pull/2#discussion_r4327775139) | 方法及命令行均传递 housing 偏好 | shared / any 与 CLI 覆盖值 |
| [4327775155](https://github.com/KardeniaPoyu/xhs-rental-hunter/pull/2#discussion_r4327775155) | 报告、方法、analyze、run-all 共享预算默认值 | 各入口默认和显式参数一致 |
| [4327775164](https://github.com/KardeniaPoyu/xhs-rental-hunter/pull/2#discussion_r4327775164) | 警示案例来自条件冲突或信息不足状态 | 普通候选不占用警示案例 |
| [4327775177](https://github.com/KardeniaPoyu/xhs-rental-hunter/pull/2#discussion_r4327775177) | 完整输入记录去重，以源索引映射分析数据 | 重复 ID、相同文案不同元数据 |
| [4327775199](https://github.com/KardeniaPoyu/xhs-rental-hunter/pull/2#discussion_r4327775199) | 补全否定词，合租否定不作为肯定 | 不接受、不考虑等表述 |
| [4327775208](https://github.com/KardeniaPoyu/xhs-rental-hunter/pull/2#discussion_r4327775208) | 识别每月前缀 | 每月3800元 |

测试位于 `tests/test_review_regressions.py`、`tests/test_bridge_security.py`、`tests/test_bridge_auth_client.py`。Windows 本地 65 项通过；完整平台矩阵见 PR Checks。

协议 v2 需服务端、CLI、扩展一起更新。HMAC 提供认证与完整性，不提供加密；只监听本机。真实 Chrome 登录态采集及旧扩展整体审计仍待完成。

