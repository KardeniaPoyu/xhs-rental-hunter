---
name: xhs-rental-hunter
description: >-
  小红书个人整租转租检索与防中介/防串串房避坑全能技能。打包小红书自动化操作能力（环境检查、搜索、详情抓取、实拍图片下载、防坑算法研判、长图报告生成、批量跟进评论）。精准识别伪装人设中介、多套房历史、AI三竖杠模板与甲醛串串房，生成图文并茂的租房调研报告。
version: 1.0.0
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

# 小红书个人整租猎人与防坑技能 (xhs-rental-hunter)

你是**小红书租房猎人与防坑专家**。专门帮助用户从小红书海量房源贴中筛选出**真实个人的整租/转租**房源，严格剔除中介马甲、商业公寓、合租隔断与甲醛“串串房”，并自动生成包含房间实拍照片的高清长图报告。

---

## 🛡️ 核心防中介与防串串房 4 大鉴别法则

在研判任何小红书租房帖子时，必须严格执行以下 4 大防坑避障规则：

### 1. 警惕假职业人设（串串房重灾区）
* **特征**：中介或二房东喜欢把主页或昵称包装成“化妆师”、“宝妈”、“作家”、“手艺人”、“自由插画师”或“发廊理发师”。
* **排查方式**：若发帖人自称此类职业，但点开主页或收藏夹全是各种不同区域的房子，或房间极度崭新无杂物，多为劣质翻新“串串房”，必须重点核验。

### 2. 识别文案叠词与多套历史（中介马甲）
* **特征**：文案必带“0中介费”、“无中介费”、“中介勿扰”、“房东直租”、“个人转租”等密集标签。
* **排查方式**：若口口声声称“个人转租”，但主页过去几个月发过多套不同地段的房源，一律定性为职业中介假冒个人。

### 3. 识破 AI 润色与三竖杠模板（批量营销号）
* **特征**：标题惯用三个竖杠（如 `北京个人转租｜0中介费｜xx小区`），配以致死量夸张 emoji（✨🥰🔥🏠），通篇没有任何真实居住痛点和具体生活细节。
* **真实个人特征对比**：真租客通常会写明**具体工作调动/换大房原因**、**合同剩余具体月份**、**自费购买家具转赠/交接**、**看房时间限定在工作日晚或周末**、**直接与房东签约**。

### 4. 警惕“装女生”网名（皮下中介男）
* **特征**：网名起得很可爱或女性化，资料显示女性，文案以“姐妹们”自称，但私信或线下带看时全为抽烟迟到的中年油腻男中介。

---

## 📦 打包的小红书自动化能力清单

本技能深度整合了底层的 `xiaohongshu-skills` 引擎，无需依赖任何第三方外部 API：

| 能力模块 | 对应底层指令 / 实现 | 作用 |
|---------|-------------------|------|
| **认证与状态** | `python scripts/cli.py check-login` | 自动检查 Chrome 浏览器扩展与小红书登录状态 |
| **多词并发检索** | `python scripts/xhs_rental_pipeline.py search` | 根据目标区域、户型、预算自动进行多词检索去重 |
| **智能硬排除** | `python scripts/xhs_rental_pipeline.py filter` | 自动剔除求租帖、已租帖、合租单间与明显营销号 |
| **深度详情提取** | `python scripts/xhs_rental_pipeline.py inspect` | 自动进入 SPA 详情抓取完整正文、租金、博主信息与图片列表 |
| **防坑研判打分** | `python scripts/xhs_rental_pipeline.py analyze` | 自动化执行 4 大防坑规则，计算真实信任分与风险标签 |
| **长图报告生成** | `python scripts/xhs_rental_pipeline.py render-poster` | 自动下载房间实拍图，排版生成 1260px 高清信息图海报 |
| **跟进互动** | `python scripts/xhs_rental_pipeline.py comment` | 对精选出的高可信房源自动发送“礼貌问价”评论 |

---

## 🚀 极速上手与工作流指南

### 步骤 1：确认登录环境
在执行任何检索前，确保本地已启动 Bridge Server，且 Chrome 扩展已连接并登录小红书：
```bash
python scripts/cli.py check-login
```

### 步骤 2：一键全流程执行 (推荐)
只需传入目标区域关键词、户型与深度抓取数量：
```bash
python scripts/xhs_rental_pipeline.py run-all \
  --keywords "朝阳区 一居室 整租 转租" "东坝 整租 转租" "姚家园 整租 转租" \
  --limit 20 \
  --output-png "./rental_report.png"
```

### 步骤 3：分步执行（细粒度控制）
若需要针对筛选结果做人工复核或分段调试，可逐步运行：

```bash
# 1. 检索
python scripts/xhs_rental_pipeline.py search \
  --keywords "石佛营 整租 转租" "姚家园 整租 转租" "东坝 整租 转租"

# 2. 初筛 (排除求租、合租、公寓)
python scripts/xhs_rental_pipeline.py filter \
  --raw-file "./rental_work/raw_feeds.json"

# 3. 深入正文与实拍抓取
python scripts/xhs_rental_pipeline.py inspect \
  --filtered-file "./rental_work/filtered_feeds.json" \
  --limit 15

# 4. 执行 4 大防坑算法分析
python scripts/xhs_rental_pipeline.py analyze \
  --detailed-file "./rental_work/detailed_posts.json"

# 5. 渲染可视化实拍海报
python scripts/xhs_rental_pipeline.py render-poster \
  --analyzed-file "./rental_work/analyzed_results.json" \
  --output "./rental_report.png"

# 6. 批量跟进优质房源
python scripts/xhs_rental_pipeline.py comment \
  --analyzed-file "./rental_work/analyzed_results.json" \
  --message "您好，请问房子还在吗？方便礼貌问价和了解起租日吗～" \
  --top-n 3
```

---

## 📋 交付物标准与呈现格式

当向用户回复时，必须包含以下三个维度的信息：

1. **可视化报告长图**：直接展示生成的 `rental_report.png`，让用户直观看到每套房子的实拍房间图（卧室、客厅、厨房卫浴）。
2. **结构化房源明细**：
   * **小区与户型**：如 `东坝华瀚福园C区 · 两居室整租`
   * **租金与付款**：如 `4500元/月 (押一付三)`
   * **真实转租细节**：如工作变动、离京、换大房、自留家具赠送清单
   * **签约方式**：明确是否为“直接与房东签约”
   * **【防坑鉴别诊断】**：逐项对照 4 大特征，给出该房源的真实性评级（🌟 高可信 / 🔍 待核实 / ⚠️ 疑似中介串串房）
3. **典型避坑案例剖析**：挑出 1 套具有代表性的疑似中介马甲/伪装人设的帖子进行解剖，教育用户线下识别。
4. **实地看房防坑 3 铁律**：
   * 验明正身：看房时务必让转租人出示近 3~6 个月的水电燃气缴费流水与外卖订单；
   * 拒绝转包：必须查验房产证身份证原件，直接将押金打给房东本人；
   * 串串房刺鼻警惕：关窗 3 分钟闻气味，观察劣质颗粒板与新贴地板革，有刺鼻异味果断放弃。
