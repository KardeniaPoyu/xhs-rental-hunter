"""小红书原生文字封面生成器 (XHS Native Cover Generator)

生成无"AI味"的小红书 3:4 (1080x1440) 原生高审美文字封面。
支持多种经典爆款风格：
  1. minimal_light: 极简冷淡大字报（荧光笔划线强调、高对比排版）
  2. memo: 真实 iOS 备忘录手感（便签复盘、打勾清单）
  3. dark_terminal: 程序员极客终端 / VS Code 窗口质感
  4. bold_contrast: 高对比冲击力大字报（黄黑/红白醒目配色）

渲染引擎：基于 Chrome Headless 导出像素级高保真 PNG，字体细腻无锯齿。
"""

from __future__ import annotations

import argparse
import html
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

logger = logging.getLogger("xhs-cover")


def _find_chrome_path() -> str:
    """查找本地 Chrome 可执行文件路径。"""
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    # Unix / macOS fallback
    for name in ["google-chrome", "chromium-browser", "chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]:
        if os.path.exists(name):
            return name
    raise FileNotFoundError("未找到 Chrome 浏览器，请确认已安装 Google Chrome")


def render_html_to_image(html_content: str, output_path: str, width: int = 1080, height: int = 1440) -> str:
    """使用 Chrome Headless 将 HTML 渲染为指定尺寸的无损 PNG 图片。"""
    chrome_path = _find_chrome_path()
    output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with tempfile.NamedTemporaryFile("w", suffix=".html", encoding="utf-8", delete=False) as f:
        f.write(html_content)
        temp_html = f.name

    try:
        file_url = "file:///" + temp_html.replace("\\", "/")
        cmd = [
            chrome_path,
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--window-size={width},{height}",
            f"--screenshot={output_path}",
            file_url,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError(f"Chrome 截图生成失败: {res.stderr}")
        return output_path
    finally:
        try:
            os.remove(temp_html)
        except OSError:
            pass


# ─── 模板 1: 极简冷淡白底大字报 (minimal_light) ──────────────────────────────────

def _render_minimal_light(
    tag: str,
    title: str,
    highlight: str | None,
    points: Sequence[str],
    footer: str,
    author: str,
) -> str:
    escaped_title = html.escape(title).replace("\n", "<br>")
    if highlight:
        escaped_hl = html.escape(highlight)
        escaped_title = escaped_title.replace(
            escaped_hl,
            f'<span class="highlight">{escaped_hl}</span>'
        )

    points_html = "".join(
        f'<div class="point-item"><span class="dot">✦</span><span>{html.escape(p)}</span></div>'
        for p in points
    )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    width: 1080px;
    height: 1440px;
    background: #F4F4F3;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
    display: flex;
    justify-content: center;
    align-items: center;
    padding: 48px;
    -webkit-font-smoothing: antialiased;
  }}
  .card {{
    width: 100%;
    height: 100%;
    background: #FFFFFF;
    border-radius: 40px;
    box-shadow: 0 24px 60px rgba(0, 0, 0, 0.05), 0 2px 8px rgba(0, 0, 0, 0.02);
    border: 1.5px solid #EAEAEA;
    padding: 80px 72px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    position: relative;
    overflow: hidden;
  }}
  .card::before {{
    content: "";
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 12px;
    background: #111111;
  }}
  .header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}
  .tag-badge {{
    display: inline-flex;
    align-items: center;
    padding: 10px 24px;
    background: #F2F2F0;
    color: #222222;
    border-radius: 100px;
    font-size: 28px;
    font-weight: 700;
    letter-spacing: 0.5px;
  }}
  .author-tag {{
    font-size: 24px;
    color: #888888;
    font-weight: 500;
  }}
  .main-content {{
    margin-top: 40px;
    flex-grow: 1;
    display: flex;
    flex-direction: column;
    justify-content: center;
  }}
  .title {{
    font-size: 88px;
    font-weight: 900;
    line-height: 1.25;
    color: #111111;
    letter-spacing: -1.5px;
    margin-bottom: 50px;
  }}
  .highlight {{
    background: linear-gradient(180deg, transparent 55%, #FFE14D 55%);
    display: inline;
    padding: 0 8px;
  }}
  .divider {{
    width: 80px;
    height: 6px;
    background: #111111;
    border-radius: 4px;
    margin-bottom: 48px;
  }}
  .points-box {{
    display: flex;
    flex-direction: column;
    gap: 24px;
    background: #F9F9F8;
    padding: 36px 40px;
    border-radius: 24px;
    border: 1px solid #EDEDEC;
  }}
  .point-item {{
    display: flex;
    align-items: flex-start;
    gap: 16px;
    font-size: 34px;
    font-weight: 600;
    color: #2D3748;
    line-height: 1.4;
  }}
  .dot {{
    color: #FF5A5F;
    font-size: 28px;
    margin-top: 4px;
  }}
  .footer {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-top: 1.5px solid #F0F0F0;
    padding-top: 36px;
  }}
  .footer-text {{
    font-size: 26px;
    font-weight: 600;
    color: #666666;
    letter-spacing: 0.5px;
  }}
  .footer-stamp {{
    background: #111111;
    color: #FFFFFF;
    padding: 8px 20px;
    border-radius: 8px;
    font-size: 22px;
    font-weight: 800;
    letter-spacing: 1px;
  }}
</style>
</head>
<body>
  <div class="card">
    <div class="header">
      <div class="tag-badge">{html.escape(tag)}</div>
      <div class="author-tag">{html.escape(author)}</div>
    </div>
    <div class="main-content">
      <div class="title">{escaped_title}</div>
      <div class="divider"></div>
      <div class="points-box">
        {points_html}
      </div>
    </div>
    <div class="footer">
      <div class="footer-text">{html.escape(footer)}</div>
      <div class="footer-stamp">干货复盘</div>
    </div>
  </div>
</body>
</html>"""


# ─── 模板 2: 真实 iOS 备忘录风 (memo) ───────────────────────────────────────────

def _render_memo(
    tag: str,
    title: str,
    highlight: str | None,
    points: Sequence[str],
    footer: str,
    author: str,
) -> str:
    escaped_title = html.escape(title).replace("\n", "<br>")
    items_html = "".join(
        f'''<div class="memo-row">
            <div class="checkbox">✓</div>
            <div class="row-text">{html.escape(p)}</div>
        </div>'''
        for p in points
    )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    width: 1080px;
    height: 1440px;
    background: #F7F6F2;
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "SF Pro Text", "Microsoft YaHei", sans-serif;
    padding: 56px;
    display: flex;
    justify-content: center;
    align-items: center;
  }}
  .memo-sheet {{
    width: 100%;
    height: 100%;
    background: #FFFFFF;
    border-radius: 36px;
    box-shadow: 0 16px 40px rgba(0, 0, 0, 0.04);
    border: 1px solid #EBE9E1;
    padding: 72px 64px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
  }}
  .memo-nav {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    color: #E6A23C;
    font-size: 32px;
    font-weight: 600;
  }}
  .back-btn {{
    display: flex;
    align-items: center;
    gap: 8px;
    color: #D97706;
  }}
  .memo-folder {{
    color: #9CA3AF;
    font-size: 26px;
    font-weight: 500;
  }}
  .date-meta {{
    font-size: 26px;
    color: #9CA3AF;
    margin-top: 36px;
    font-weight: 400;
  }}
  .memo-body {{
    flex-grow: 1;
    margin-top: 24px;
  }}
  .memo-title {{
    font-size: 80px;
    font-weight: 800;
    line-height: 1.25;
    color: #1F2937;
    margin-bottom: 48px;
    letter-spacing: -1px;
  }}
  .memo-tag {{
    display: inline-block;
    padding: 8px 20px;
    background: #FEF3C7;
    color: #92400E;
    border-radius: 12px;
    font-size: 26px;
    font-weight: 700;
    margin-bottom: 32px;
  }}
  .memo-list {{
    display: flex;
    flex-direction: column;
    gap: 28px;
    margin-top: 20px;
  }}
  .memo-row {{
    display: flex;
    align-items: center;
    gap: 20px;
    background: #FDFBF7;
    padding: 22px 28px;
    border-radius: 18px;
    border: 1px solid #F5F1E6;
  }}
  .checkbox {{
    width: 44px;
    height: 44px;
    border-radius: 50%;
    background: #D97706;
    color: #FFFFFF;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 26px;
    font-weight: 900;
    flex-shrink: 0;
  }}
  .row-text {{
    font-size: 34px;
    font-weight: 600;
    color: #374151;
    line-height: 1.4;
  }}
  .memo-footer {{
    border-top: 2px dashed #E5E7EB;
    padding-top: 32px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}
  .footer-tip {{
    font-size: 26px;
    color: #6B7280;
    font-weight: 500;
  }}
  .memo-badge {{
    font-size: 24px;
    font-weight: 700;
    color: #D97706;
    background: #FFFBEB;
    padding: 8px 18px;
    border-radius: 8px;
  }}
</style>
</head>
<body>
  <div class="memo-sheet">
    <div>
      <div class="memo-nav">
        <div class="back-btn">‹ 备忘录</div>
        <div class="memo-folder">2026秋招 · {html.escape(author)}</div>
      </div>
      <div class="date-meta">秋招实战复盘记事</div>
    </div>
    <div class="memo-body">
      <div class="memo-tag">#{html.escape(tag)}</div>
      <div class="memo-title">{escaped_title}</div>
      <div class="memo-list">
        {items_html}
      </div>
    </div>
    <div class="memo-footer">
      <div class="footer-tip">✍️ {html.escape(footer)}</div>
      <div class="memo-badge">个人私藏笔记</div>
    </div>
  </div>
</body>
</html>"""


# ─── 模板 3: 程序员 IDE 终端极客风 (dark_terminal) ─────────────────────────────

def _render_dark_terminal(
    tag: str,
    title: str,
    highlight: str | None,
    points: Sequence[str],
    footer: str,
    author: str,
) -> str:
    escaped_title = html.escape(title).replace("\n", "<br>")
    lines_html = "".join(
        f'''<div class="code-line">
            <span class="line-num">{idx+1:02d}</span>
            <span class="kw">yield</span> <span class="str">"{html.escape(p)}"</span>;
        </div>'''
        for idx, p in enumerate(points)
    )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    width: 1080px;
    height: 1440px;
    background: #0D1117;
    font-family: "Consolas", "Courier New", "JetBrains Mono", -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", monospace;
    padding: 48px;
    display: flex;
    justify-content: center;
    align-items: center;
  }}
  .window {{
    width: 100%;
    height: 100%;
    background: #161B22;
    border-radius: 28px;
    border: 1px solid #30363D;
    box-shadow: 0 30px 80px rgba(0, 0, 0, 0.6);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }}
  .title-bar {{
    background: #0D1117;
    height: 72px;
    padding: 0 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 1px solid #30363D;
  }}
  .traffic-lights {{
    display: flex;
    gap: 14px;
  }}
  .circle {{
    width: 22px;
    height: 22px;
    border-radius: 50%;
  }}
  .close {{ background: #FF5F56; }}
  .minimize {{ background: #FFBD2E; }}
  .maximize {{ background: #27C93F; }}
  .file-name {{
    color: #8B949E;
    font-size: 24px;
    font-family: monospace;
  }}
  .branch {{
    color: #58A6FF;
    font-size: 22px;
  }}
  .content {{
    padding: 64px;
    flex-grow: 1;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
  }}
  .terminal-tag {{
    color: #7EE787;
    font-size: 28px;
    font-weight: 700;
    margin-bottom: 24px;
  }}
  .main-headline {{
    font-size: 82px;
    font-weight: 900;
    color: #F0F6FC;
    line-height: 1.25;
    letter-spacing: -1px;
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
  }}
  .code-editor {{
    background: #0D1117;
    border: 1px solid #30363D;
    border-radius: 20px;
    padding: 36px 32px;
    margin: 40px 0;
    display: flex;
    flex-direction: column;
    gap: 20px;
  }}
  .code-line {{
    font-size: 32px;
    line-height: 1.5;
    display: flex;
    align-items: center;
    gap: 20px;
  }}
  .line-num {{
    color: #484F58;
    user-select: none;
    font-size: 26px;
    width: 36px;
  }}
  .kw {{ color: #FF7B72; font-weight: 700; }}
  .str {{ color: #A5D6FF; font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif; font-weight: 600; }}
  .term-footer {{
    border-top: 1px solid #30363D;
    padding-top: 32px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    color: #8B949E;
    font-size: 24px;
  }}
  .status-badge {{
    background: #238636;
    color: #FFFFFF;
    padding: 8px 20px;
    border-radius: 6px;
    font-weight: 700;
    font-size: 22px;
  }}
</style>
</head>
<body>
  <div class="window">
    <div class="title-bar">
      <div class="traffic-lights">
        <div class="circle close"></div>
        <div class="circle minimize"></div>
        <div class="circle maximize"></div>
      </div>
      <div class="file-name">interview_review.cs — git:(main)</div>
      <div class="branch">{html.escape(author)}</div>
    </div>
    <div class="content">
      <div>
        <div class="terminal-tag">// {html.escape(tag)}</div>
        <div class="main-headline">{escaped_title}</div>
      </div>
      <div class="code-editor">
        {lines_html}
      </div>
      <div class="term-footer">
        <div>$ {html.escape(footer)}</div>
        <div class="status-badge">PASS 100%</div>
      </div>
    </div>
  </div>
</body>
</html>"""


# ─── 模板 4: 醒目高反差大字报 (bold_contrast) ────────────────────────────────────

def _render_bold_contrast(
    tag: str,
    title: str,
    highlight: str | None,
    points: Sequence[str],
    footer: str,
    author: str,
) -> str:
    escaped_title = html.escape(title).replace("\n", "<br>")
    points_html = "".join(
        f'<div class="point-pill"><span class="badge">KEY</span> {html.escape(p)}</div>'
        for p in points
    )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    width: 1080px;
    height: 1440px;
    background: #F6D84C;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    padding: 48px;
    display: flex;
    justify-content: center;
    align-items: center;
  }}
  .box {{
    width: 100%;
    height: 100%;
    background: #111111;
    border-radius: 36px;
    padding: 72px;
    color: #FFFFFF;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    box-shadow: 0 24px 60px rgba(0,0,0,0.25);
  }}
  .tag-row {{
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}
  .yellow-tag {{
    background: #F6D84C;
    color: #111111;
    padding: 10px 24px;
    border-radius: 100px;
    font-size: 28px;
    font-weight: 900;
  }}
  .author {{
    font-size: 26px;
    color: #A0A0A0;
    font-weight: 500;
  }}
  .title-section {{
    margin: 40px 0;
  }}
  .bold-title {{
    font-size: 92px;
    font-weight: 900;
    line-height: 1.22;
    letter-spacing: -2px;
    color: #FFFFFF;
  }}
  .yellow-text {{
    color: #F6D84C;
  }}
  .points-col {{
    display: flex;
    flex-direction: column;
    gap: 24px;
  }}
  .point-pill {{
    background: #222222;
    border: 1px solid #333333;
    padding: 24px 32px;
    border-radius: 20px;
    font-size: 34px;
    font-weight: 700;
    color: #E2E8F0;
    display: flex;
    align-items: center;
    gap: 20px;
  }}
  .badge {{
    background: #F6D84C;
    color: #111111;
    font-size: 20px;
    font-weight: 900;
    padding: 4px 12px;
    border-radius: 6px;
  }}
  .bottom-bar {{
    border-top: 1px solid #333333;
    padding-top: 32px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    color: #A0A0A0;
    font-size: 26px;
    font-weight: 600;
  }}
</style>
</head>
<body>
  <div class="box">
    <div class="tag-row">
      <div class="yellow-tag">{html.escape(tag)}</div>
      <div class="author">{html.escape(author)}</div>
    </div>
    <div class="title-section">
      <div class="bold-title">{escaped_title}</div>
    </div>
    <div class="points-col">
      {points_html}
    </div>
    <div class="bottom-bar">
      <div>{html.escape(footer)}</div>
      <div>小红书独家复盘</div>
    </div>
  </div>
</body>
</html>"""


# ─── 主调度函数 ─────────────────────────────────────────────────────────────────

TEMPLATES = {
    "minimal_light": _render_minimal_light,
    "memo": _render_memo,
    "dark_terminal": _render_dark_terminal,
    "bold_contrast": _render_bold_contrast,
}


def generate_cover(
    style: str,
    output_path: str,
    tag: str,
    title: str,
    points: Sequence[str],
    highlight: str | None = None,
    footer: str = "真实经历复盘 · 攒人品求Offer",
    author: str = "小菲在coding",
) -> str:
    """生成小红书原生文字封面并保存到本地。

    Args:
        style: 风格模版名称 ('minimal_light', 'memo', 'dark_terminal', 'bold_contrast')
        output_path: 目标 PNG 文件路径
        tag: 顶部标签 (如 '2026秋招 · 客户端一面')
        title: 核心醒目标题 (支持 \\n 换行)
        points: 3~4 条核心亮点清单
        highlight: 标题中需要荧光/高亮强调的词组
        footer: 底部说明文案
        author: 作者署名

    Returns:
        生成的图片绝对路径。
    """
    if style not in TEMPLATES:
        raise ValueError(f"未知风格 '{style}'，支持的风格: {list(TEMPLATES.keys())}")

    renderer = TEMPLATES[style]
    html_str = renderer(
        tag=tag,
        title=title,
        highlight=highlight,
        points=points,
        footer=footer,
        author=author,
    )
    return render_html_to_image(html_str, output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="小红书原生文字封面生成器")
    parser.add_argument(
        "--style",
        default="minimal_light",
        choices=list(TEMPLATES.keys()),
        help="封面风格模板 (default: minimal_light)",
    )
    parser.add_argument("--output", "-o", required=True, help="输出 PNG 路径")
    parser.add_argument("--tag", default="秋招面经复盘", help="顶部类别标签")
    parser.add_argument("--title", required=True, help="大标题 (支持 \\n 换行)")
    parser.add_argument("--highlight", help="标题中需高亮的关键词")
    parser.add_argument("--points", nargs="+", default=[], help="亮点清单列表 (3~4条最佳)")
    parser.add_argument("--footer", default="真实经历复盘 · 攒人品求Offer", help="底部说明文案")
    parser.add_argument("--author", default="小菲在coding", help="作者署名")

    args = parser.parse_args()
    title = args.title.replace("\\n", "\n")
    out = generate_cover(
        style=args.style,
        output_path=args.output,
        tag=args.tag,
        title=title,
        points=args.points,
        highlight=args.highlight,
        footer=args.footer,
        author=args.author,
    )
    print(f"封面已成功生成: {out}")


if __name__ == "__main__":
    main()
