---
name: xhs-rental-hunter
description: >-
  在小红书上找真实个人整租/转租房源并识别中介马甲、二房东与串串房。用户说“小红书找房/租房/整租/转租”、
  “帮我筛掉中介”、“看看这几个房源帖靠不靠谱”、“生成租房报告/长图”，或给出城市+区域+预算想租房时使用。
  通过用户已登录的 Chrome（扩展 + 本地 bridge）检索、抓取详情与发帖人主页，规则初判后由 Claude 逐条复核，
  输出带实拍图的长图报告与看房追问清单。Use for finding genuine individual rentals on Xiaohongshu (RED)
  and screening out agents, sublessors and flipped "chuanchuan" apartments.
version: 2.0.0
metadata:
  openclaw:
    requires:
      bins:
        - python3
        - uv
    emoji: "🏡"
    os:
      - darwin
      - linux
      - windows
---

# 小红书个人整租猎人

目标：从小红书房源帖里找出**真实个人**发布的**整租 / 转租**房源，剔除中介马甲、二房东、商业公寓、
合租单间和劣质翻新的串串房，交给用户一份能直接拿去约看房的结论。

分工原则：**脚本做机械活，Claude 做判断。** 脚本负责检索、抓取、抽取价格户型、关键词初判和画图；
“这个人到底是不是中介”需要读原文、看主页、看口吻 —— 这一步必须由你（Claude）完成，不能直接照搬规则分。

所有命令在仓库根目录执行（本文件与根目录 SKILL.md 内容一致）。`<W>` 指工作目录（默认 `./rental_work`）。

---

## 第 0 步：确认需求（缺什么问什么，一次问完）

| 需求 | 用途 | 未提供时 |
|---|---|---|
| 城市 + 区域/商圈/地铁站/小区 | 生成关键词、`--area-keywords` | **必须问** |
| 预算区间 | `--budget-min/--budget-max` | 问；用户说“都行”就不设 |
| 户型 | `--bedrooms 开间 一居 两居` | 默认不限 |
| 入住时间 | 对照 `move_in` / 租期 | 默认不限 |
| 能否接受转租（非原始合同） | 报告里标注 | 默认接受，但优先“可与房东重签” |

用户已经在消息里说清楚的，不要再问。

## 第 1 步：生成关键词（3~6 个）

个人房东/租客的帖子很少写“房东直租”这种词 —— 那恰恰是中介最爱用的。推荐组合：

- `{商圈/地铁站} 整租 转租`、`{小区名} 转租`、`{商圈} 一居 转租`
- 大区兜底：`{区} 个人转租 整租`
- 热门城区加 `--publish-time 一周内`；冷门区域用默认（不限）

## 第 2 步：跑流水线

```bash
uv run python scripts/xhs_rental_pipeline.py doctor          # 自检：bridge/扩展/字体
uv run python scripts/cli.py check-login                      # 未登录会给出二维码，让用户扫码

uv run python scripts/xhs_rental_pipeline.py run-all \
  --keywords "东坝 整租 转租" "褡裢坡 一居 转租" "朝阳 个人转租 整租" \
  --budget-min 3500 --budget-max 5000 --bedrooms 一居 两居 \
  --max-age-days 30 --area-keywords 东坝 褡裢坡 --city 北京 \
  --limit 15 --author-limit 8
```

`run-all` = search → filter → inspect → check-authors → analyze → digest → render-poster（**不含评论**）。
各步可单独运行，参数见 `--help`；详情与主页均有缓存，重跑会跳过已完成部分。

**看退出码与最后一行 JSON：**

- `0`：成功，`counts` 给出高可信/待核实/疑似/排除数量。
- `3`：小红书要求人工验证。**立即停下**，请用户在 Chrome 里打开小红书完成验证，几分钟后重跑同一命令。
  不要改小 `--delay`、不要换方式绕过。
- `2`：其他错误，按 `error`/`hints` 排查（见文末）。

结果太少时：换关键词再 `search`（结果会累积去重），然后 `filter → inspect → analyze → digest`。
保持 `--limit` ≤ 20、`--author-limit` ≤ 10：够用就停，不做大批量抓取。

## 第 3 步：逐条复核（核心）

读 `<W>/review.md`。每条包含：抽取的价格/户型/租期、命中的正向与风险证据（带原文片段）、
主页房源帖数量、正文、建议追问。按 [references/anti-agent-rules.md](../../references/anti-agent-rules.md)
的四条法则给出**你自己的**结论，特别留意规则分看不出来的情况：

- 文案通顺、细节丰富，但读起来像模板改写（每篇结构一样、只换小区名）→ 批量营销
- 昵称/头像“个人化”，但口吻是“宝子们有需要的滴滴我”“还有其他房源” → 人设与口吻不一致
- 规则判“排除”的帖子是否误杀（看 review.md 末尾“已排除”表）
- 价格明显低于同区域行情 → 引流钓鱼
- 主页未核验或核验失败的高分帖：结论最多给“待核实”
- “跨账号雷同”要看另一个账号是谁（昵称“小号”+本人日常 ≠ 批量中介）；IP 属地不符可能只是人在外地
- 公租房/保障房/人才房（如“燕保”）转租有被清退风险，即使发帖人真实也要提醒

然后把精选结果写入 `<W>/curated.json`（格式：[references/curated-schema.md](../../references/curated-schema.md)），
一般 3~6 套推荐 + 1 个典型避坑案例，重新出图：

```bash
uv run python scripts/xhs_rental_pipeline.py render-poster --title "北京朝阳 · 东坝个人整租精选"
```

渲染后用 Read 打开 PNG 检查一遍（文字是否截断、图片是否加载）。

## 第 4 步：交付

1. **长图报告**（`rental_report.png`）。
2. **逐套结论**（与长图一致，便于复制）：小区·户型 / 租金与付款方式 / 转租原因 / 签约方式与剩余租期 /
   可信度结论 + 关键证据 / 看房前要追问的 2~3 个问题 / 原帖链接。
3. **一个避坑案例拆解**：指出具体是哪几条特征露馅，帮用户以后自己识别。
4. **线下看房三条铁律**：核验转租人确实住在这里（本人名下近几个月水电燃气缴费或该地址的订单记录）；
   见房东本人、核对房产证与身份证，钱只打给房东同名账户；新装修房关窗闻味、看板材，可要求甲醛检测。

结论措辞保持克制：“疑似”“待核实”，不要断言某个具体的人是骗子。

## 评论与私信：必须先得到用户明确同意

评论会以用户的账号公开发出。流程：

1. 先预览（不会发送）：
   `uv run python scripts/xhs_rental_pipeline.py comment --ids <id1> <id2> --message "你好，请问房子还在吗？可以和房东直接签吗？"`
2. 把目标帖子和评论原文给用户看，等用户明确说“发”。
3. 再加 `--confirm` 执行。已评论过的帖子会自动跳过；每条之间间隔 20~40 秒。

优先建议用户自己私信联系（更自然，也不会让对方觉得被群发）。不要替用户发私信、加微信或做任何承诺。

## 节制与隐私

- 只抓与本次需求相关的少量帖子，保持默认间隔；遇到验证就停。
- 报告供用户个人看房使用。不要把发帖人主页信息、截图汇总后公开发布或用于其他目的。
- 不修改 `scripts/xhs/` 中的风控相关逻辑。

## 故障排查

| 现象 | 处理 |
|---|---|
| `doctor` 显示 server=false | 运行 `python scripts/bridge_server.py`，或执行一次 `cli.py check-login`（会自动拉起） |
| server=true 但 extension=false | Chrome → `chrome://extensions` → 开发者模式 → 加载 `extension/`；打开 xiaohongshu.com |
| `check-login` 返回未登录 | 把二维码图片给用户扫码，再运行 `cli.py wait-login` |
| 退出码 3 / “需要扫码验证” | 用户在浏览器完成验证后，等几分钟重跑同一命令 |
| 海报报“未找到中文字体” | 设置 `XHS_FONT=/path/to/font.ttc`，或 Linux 安装 `fonts-noto-cjk` |
| 检索结果 0 | 关键词过长/过冷门 → 拆短、换商圈或地铁站名 |
| 价格抽取为空 | 帖子未写价格或写在图片里 → 在 curated.json 里用 `price_text` 手动填写或写“价格私询” |

更多子能力（发布、互动、登录等）见 `skills/` 下的其他 SKILL.md。
