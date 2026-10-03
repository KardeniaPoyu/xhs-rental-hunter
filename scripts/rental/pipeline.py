"""租房流水线编排：检索 → 初筛 → 详情 → 作者主页核验 → 研判 → 复核摘要 → 海报 → （确认后）评论。

浏览器操作全部复用 ``xhs`` 库中经过验证的函数（search_feeds / get_feed_detail /
get_user_profile / post_comment），本模块只负责编排、缓存与断点续跑。

工作目录结构（默认 ./rental_work）::

    requirements.json      本次需求（预算/户型/时效/区域关键词）
    raw_feeds.json         检索原始卡片
    filtered_feeds.json    初筛保留
    excluded_feeds.json    初筛排除（含原因，便于复核误杀）
    notes/<id>.json        单帖详情缓存（断点续跑）
    authors/<uid>.json     作者主页缓存
    detailed_posts.json    详情汇总
    analyzed_results.json  研判结果（含证据）
    review.md              给 Claude/用户复核的精简摘要
    curated.json           （可选）Claude 复核后写入的精选清单，海报优先使用
    comments_log.json      已评论记录（防重复）
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .rules import AuthorHistory, Requirements, analyze_author_history, assess, prefilter_card

logger = logging.getLogger("rental")

VERIFY_HINTS = ("验证", "扫码", "风控", "频繁", "安全限制")


class VerificationRequiredError(RuntimeError):
    """小红书要求人工验证：立即停止，保留进度，提示用户在浏览器中完成验证后重跑。"""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def _is_verification(err: Exception) -> bool:
    return any(h in str(err) for h in VERIFY_HINTS)


class BridgeUnavailableError(RuntimeError):
    """bridge server 或浏览器扩展不可用：继续逐条重试没有意义，直接中止。"""


def _check_fatal(err: Exception) -> None:
    if isinstance(err, BridgeUnavailableError):
        raise err
    msg = str(err)
    if (
        "无法连接到 bridge" in msg
        or "扩展未连接" in msg
        or "extension not connected" in msg.lower()
    ):
        raise BridgeUnavailableError(
            "无法连接浏览器：请先运行 doctor 自检（启动 bridge_server.py 并在 Chrome 中启用扩展）"
        ) from err


def _require(path: Path, hint: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"找不到 {path.name}，请先运行 {hint}")


class RentalPipeline:
    def __init__(
        self,
        work_dir: str = "./rental_work",
        bridge_url: str = "ws://localhost:9333",
        delay: tuple[float, float] = (2.5, 5.0),
        page_factory: Callable[[], Any] | None = None,
        now_ms: int | None = None,
    ) -> None:
        self.work = Path(work_dir).resolve()
        self.work.mkdir(parents=True, exist_ok=True)
        self.cache_dir = self.work / "img_cache"
        self.cache_dir.mkdir(exist_ok=True)
        self.bridge_url = bridge_url
        self.delay = delay
        self._page_factory = page_factory
        self._page: Any = None
        self.now_ms = now_ms or _now_ms()

    # ─── 基础 ──────────────────────────────────────────────────────────────

    @property
    def page(self) -> Any:
        if self._page is None:
            if self._page_factory:
                self._page = self._page_factory()
            else:
                from xhs.bridge import BridgePage

                page = BridgePage(self.bridge_url)
                if not page.is_server_running() or not page.is_extension_connected():
                    raise BridgeUnavailableError(
                        "无法连接浏览器：请先运行 doctor 自检"
                        "（启动 bridge_server.py，并在已登录小红书的 Chrome 中启用扩展）"
                    )
                self._page = page
        return self._page

    def path(self, name: str) -> Path:
        return self.work / name

    def _pause(self) -> None:
        lo, hi = self.delay
        if hi > 0:
            time.sleep(random.uniform(lo, hi))

    def save_requirements(self, req: dict[str, Any]) -> None:
        clean = {k: v for k, v in req.items() if v not in (None, [], "")}
        if clean:
            _write_json(self.path("requirements.json"), clean)

    def load_requirements(self) -> Requirements:
        return Requirements.from_dict(_read_json(self.path("requirements.json"), {}))

    # ─── 1. 检索 ───────────────────────────────────────────────────────────

    def search(
        self,
        keywords: list[str],
        per_keyword: int = 20,
        sort_by: str = "最新",
        publish_time: str = "",
    ) -> Path:
        from xhs.errors import NoFeedsError
        from xhs.search import search_feeds
        from xhs.types import FilterOption

        existing = _read_json(self.path("raw_feeds.json"), [])
        seen = {f["id"] for f in existing}
        out = list(existing)
        fo = FilterOption(sort_by=sort_by, publish_time=publish_time, note_type="图文")

        for i, kw in enumerate(keywords):
            logger.info("[检索 %d/%d] %s", i + 1, len(keywords), kw)
            try:
                try:
                    feeds = search_feeds(self.page, kw, fo)
                except ValueError as e:  # 筛选面板改版/点击失败：退回无筛选检索
                    logger.warning("  筛选条件未生效（%s），改用默认排序", e)
                    feeds = search_feeds(self.page, kw, None)
            except NoFeedsError:
                logger.warning("  关键词无结果：%s", kw)
                continue
            except Exception as e:
                if _is_verification(e):
                    _write_json(self.path("raw_feeds.json"), out)
                    raise VerificationRequiredError(str(e)) from e
                _check_fatal(e)
                logger.warning("  检索失败（%s）：%s", kw, e)
                continue

            added = 0
            for f in feeds[:per_keyword]:
                d = f.to_dict()
                if (
                    not d.get("id")
                    or d["id"] in seen
                    or d.get("modelType") not in ("note", "", None)
                ):
                    continue
                seen.add(d["id"])
                out.append(
                    {
                        "id": d["id"],
                        "xsec_token": d.get("xsecToken", ""),
                        "title": d.get("displayTitle", ""),
                        "author": d.get("user", {}).get("nickname", ""),
                        "user_id": d.get("user", {}).get("userId", ""),
                        "liked": d.get("interactInfo", {}).get("likedCount", ""),
                        "cover": d.get("cover", ""),
                        "query": kw,
                    }
                )
                added += 1
            logger.info("  新增 %d 条（累计 %d）", added, len(out))
            _write_json(self.path("raw_feeds.json"), out)
            if i < len(keywords) - 1:
                self._pause()

        _write_json(self.path("raw_feeds.json"), out)
        return self.path("raw_feeds.json")

    # ─── 2. 初筛 ───────────────────────────────────────────────────────────

    def filter_candidates(self, raw_path: str | Path | None = None) -> Path:
        src = Path(raw_path or self.path("raw_feeds.json"))
        _require(src, "search")
        feeds = _read_json(src, [])
        kept, dropped = [], []
        for c in feeds:
            reason = prefilter_card(c.get("title", ""), c.get("author", ""))
            if reason:
                dropped.append({**c, "excluded_reason": reason})
            else:
                kept.append(c)
        _write_json(self.path("filtered_feeds.json"), kept)
        _write_json(self.path("excluded_feeds.json"), dropped)
        logger.info(
            "[初筛] %d → 保留 %d，排除 %d（见 excluded_feeds.json）",
            len(feeds),
            len(kept),
            len(dropped),
        )
        return self.path("filtered_feeds.json")

    # ─── 3. 详情 ───────────────────────────────────────────────────────────

    def inspect(self, filtered_path: str | Path | None = None, limit: int = 15) -> Path:
        from xhs.feed_detail import get_feed_detail

        src = Path(filtered_path or self.path("filtered_feeds.json"))
        _require(src, "search 和 filter")
        cands = _read_json(src, [])[:limit]
        notes_dir = self.work / "notes"
        notes_dir.mkdir(exist_ok=True)
        detailed: list[dict] = []
        failures: list[dict] = []

        for i, c in enumerate(cands):
            cache = notes_dir / f"{c['id']}.json"
            if cache.exists():
                detailed.append(_read_json(cache))
                continue
            logger.info("[详情 %d/%d] %s", i + 1, len(cands), c.get("title", "")[:30])
            try:
                d = get_feed_detail(
                    self.page, c["id"], c.get("xsec_token", ""), keyword=c.get("query") or "租房"
                )
            except Exception as e:
                if _is_verification(e):
                    self._save_detailed(detailed, failures)
                    raise VerificationRequiredError(str(e)) from e
                _check_fatal(e)
                logger.warning("  跳过：%s", e)
                failures.append({"id": c["id"], "title": c.get("title", ""), "error": str(e)})
                self._pause()
                continue

            note = d.note
            rec = {
                "id": c["id"],
                "xsec_token": c.get("xsec_token", ""),
                "query": c.get("query", ""),
                "url": f"https://www.xiaohongshu.com/explore/{c['id']}",
                "title": note.title or c.get("title", ""),
                "desc": note.body or note.desc,
                "tags": note.tags,
                "time": note.time,
                "ip": note.ip_location,
                "user": {
                    "nickname": note.user.nickname or note.user.nick_name or c.get("author", ""),
                    "userId": note.user.user_id or c.get("user_id", ""),
                },
                "liked": note.interact_info.liked_count,
                "collected": note.interact_info.collected_count,
                "comment_count": note.interact_info.comment_count,
                "images": [
                    img.url_default or img.url_pre
                    for img in note.image_list
                    if img.url_default or img.url_pre
                ],
                "comments": [cm.to_dict() for cm in d.comments.list_[:20]],
            }
            _write_json(cache, rec)
            detailed.append(rec)
            self._pause()

        return self._save_detailed(detailed, failures)

    def _save_detailed(self, detailed: list[dict], failures: list[dict]) -> Path:
        _write_json(self.path("detailed_posts.json"), detailed)
        if failures:
            _write_json(self.path("inspect_failures.json"), failures)
        logger.info("[详情] 成功 %d 条，失败 %d 条", len(detailed), len(failures))
        return self.path("detailed_posts.json")

    # ─── 4. 作者主页核验（法则 2） ─────────────────────────────────────────

    def check_authors(self, detailed_path: str | Path | None = None, limit: int = 10) -> Path:
        """访问发帖人主页，统计其历史房源帖数量。只核验尚未被硬排除的帖子。"""
        from xhs.user_profile import get_user_profile

        src = Path(detailed_path or self.path("detailed_posts.json"))
        _require(src, "inspect")
        posts = _read_json(src, [])
        authors_dir = self.work / "authors"
        authors_dir.mkdir(exist_ok=True)
        req = self.load_requirements()

        # 先用不含主页信息的研判排序，优先核验最有希望的候选
        ranked = sorted(
            posts,
            key=lambda p: -self._assess(p, None, req).trust_score,
        )
        done = 0
        for p in ranked:
            uid = p.get("user", {}).get("userId")
            if not uid or done >= limit:
                continue
            cache = authors_dir / f"{uid}.json"
            if cache.exists():
                continue
            if self._assess(p, None, req).tier == "excluded":
                continue
            logger.info("[主页核验] %s", p.get("user", {}).get("nickname", uid))
            try:
                prof = get_user_profile(self.page, uid, p.get("xsec_token", ""))
                titles = [f.note_card.display_title for f in prof.feeds]
                hist = analyze_author_history(
                    prof.user_basic_info.nickname,
                    prof.user_basic_info.desc,
                    titles,
                    p.get("title", ""),
                    p.get("desc", ""),
                )
            except Exception as e:
                if _is_verification(e):
                    raise VerificationRequiredError(str(e)) from e
                _check_fatal(e)
                hist = AuthorHistory(checked=False, error=str(e)[:120])
            _write_json(cache, hist.to_dict())
            done += 1
            self._pause()
        return authors_dir

    def _history_for(self, post: dict) -> AuthorHistory | None:
        uid = post.get("user", {}).get("userId")
        if not uid:
            return None
        d = _read_json(self.work / "authors" / f"{uid}.json")
        return AuthorHistory(**d) if d else None

    # ─── 5. 研判 ───────────────────────────────────────────────────────────

    def _assess(
        self,
        p: dict,
        hist: AuthorHistory | None,
        req: Requirements,
        duplicates: list[str] | None = None,
    ):
        return assess(
            p.get("title", ""),
            p.get("desc", ""),
            author=p.get("user", {}).get("nickname", ""),
            author_id=p.get("user", {}).get("userId", ""),
            tags=p.get("tags") or [],
            comments=p.get("comments") or [],
            ip=p.get("ip", ""),
            duplicates=duplicates,
            history=hist,
            publish_ts_ms=p.get("time") or None,
            now_ts_ms=self.now_ms,
            req=req,
        )

    def analyze(self, detailed_path: str | Path | None = None) -> Path:
        src = Path(detailed_path or self.path("detailed_posts.json"))
        _require(src, "inspect")
        posts = _read_json(src, [])
        req = self.load_requirements()
        dups = find_cross_account_duplicates(posts)
        results = []
        for p in posts:
            hist = self._history_for(p)
            a = self._assess(p, hist, req, dups.get(p["id"]))
            results.append(
                {
                    "id": p["id"],
                    "url": p.get("url") or f"https://www.xiaohongshu.com/explore/{p['id']}",
                    "xsec_token": p.get("xsec_token", ""),
                    "title": p.get("title", ""),
                    "author": p.get("user", {}).get("nickname", ""),
                    "user_id": p.get("user", {}).get("userId", ""),
                    "ip": p.get("ip", ""),
                    "query": p.get("query", ""),
                    "desc": p.get("desc", ""),
                    "images": p.get("images", []),
                    "comment_count": p.get("comment_count", ""),
                    "author_history": hist.to_dict() if hist else None,
                    **a.to_dict(),
                }
            )

        tier_order = {"trusted": 0, "verify": 1, "suspect": 2, "excluded": 3}
        results.sort(key=lambda r: (tier_order[r["tier"]], len(r["mismatches"]), -r["trust_score"]))
        _write_json(self.path("analyzed_results.json"), results)
        counts = {t: sum(r["tier"] == t for r in results) for t in tier_order}
        logger.info("[研判] 共 %d 条：%s", len(results), counts)
        return self.path("analyzed_results.json")

    # ─── 6. 复核摘要（给 Claude 读） ───────────────────────────────────────

    def digest(
        self, analyzed_path: str | Path | None = None, max_items: int = 20, body_chars: int = 600
    ) -> Path:
        from .digest import build_review_markdown

        results = _read_json(Path(analyzed_path or self.path("analyzed_results.json")), [])
        md = build_review_markdown(
            results, self.load_requirements(), max_items=max_items, body_chars=body_chars
        )
        p = self.path("review.md")
        p.write_text(md, encoding="utf-8")
        return p

    # ─── 7. 海报 ───────────────────────────────────────────────────────────

    def render_poster(
        self,
        analyzed_path: str | Path | None = None,
        output: str | Path | None = None,
        curated_path: str | Path | None = None,
        title: str = "",
        subtitle: str = "",
        top_n: int = 5,
    ) -> Path | None:
        from .poster import render, select_entries

        curated_file = Path(curated_path) if curated_path else self.path("curated.json")
        if curated_file.exists():
            cur = _read_json(curated_file, {})
            if isinstance(cur, list):
                cur = {"entries": cur}
            entries = cur.get("entries", [])
            title = title or cur.get("title", "")
            subtitle = subtitle or cur.get("subtitle", "")
            # 补全 Claude 没写的字段（图片、链接等）
            by_id = {r["id"]: r for r in _read_json(self.path("analyzed_results.json"), [])}
            entries = [{**by_id.get(e.get("id"), {}), **e} for e in entries]
        else:
            results = _read_json(Path(analyzed_path or self.path("analyzed_results.json")), [])
            entries = select_entries(results, top_n=top_n)
        if not entries:
            logger.warning("[海报] 没有可展示的房源")
            return None
        out = Path(output) if output else self.path("rental_report.png")
        req = self.load_requirements()
        return render(
            entries, out, cache_dir=self.cache_dir, title=title, subtitle=subtitle, req=req
        )

    # ─── 8. 评论（默认 dry-run） ───────────────────────────────────────────

    def comment(
        self,
        message: str,
        analyzed_path: str | Path | None = None,
        ids: list[str] | None = None,
        top_n: int = 3,
        confirm: bool = False,
    ) -> list[dict]:
        """给指定帖子发评论。未传 confirm=True 时只列出将要评论的目标，不会真正发送。"""
        results = _read_json(Path(analyzed_path or self.path("analyzed_results.json")), [])
        log = _read_json(self.path("comments_log.json"), [])
        already = {x["id"] for x in log if x.get("ok")}

        if ids:
            targets = [r for r in results if r["id"] in set(ids)]
        else:
            targets = [r for r in results if r["tier"] == "trusted" and not r["mismatches"]][:top_n]
        targets = [t for t in targets if t["id"] not in already]

        plan = [
            {"id": t["id"], "title": t["title"], "author": t["author"], "message": message}
            for t in targets
        ]
        if not confirm:
            logger.info("[评论] dry-run：以下 %d 条尚未发送，确认后加 --confirm 执行", len(plan))
            for x in plan:
                logger.info("  - %s（%s）", x["title"][:30], x["author"])
            return plan

        from xhs.comment import post_comment

        for i, t in enumerate(targets):
            entry = {"id": t["id"], "title": t["title"], "message": message, "ts": _now_ms()}
            try:
                post_comment(self.page, t["id"], t.get("xsec_token", ""), message)
                entry["ok"] = True
            except Exception as e:
                entry.update(ok=False, error=str(e)[:200])
                if _is_verification(e):
                    log.append(entry)
                    _write_json(self.path("comments_log.json"), log)
                    raise VerificationRequiredError(str(e)) from e
            log.append(entry)
            _write_json(self.path("comments_log.json"), log)
            if i < len(targets) - 1:
                time.sleep(random.uniform(20, 40))  # 评论间隔放宽，避免被判定为刷评论
        return plan


def _shingles(text: str, k: int = 5) -> set[str]:
    from .extract import clean_post_text, normalize

    body = re.sub(r"#\S+", " ", clean_post_text(text))  # 话题标签不参与比对
    s = "".join(ch for ch in normalize(body) if ch.isalnum())
    return {s[i : i + k] for i in range(max(0, len(s) - k + 1))}


def find_cross_account_duplicates(
    posts: list[dict], threshold: float = 0.6
) -> dict[str, list[str]]:
    """找出正文与“其他账号”的帖子高度雷同的帖子：{帖子id: [雷同账号昵称…]}。

    同一账号重复发布不算（那是正常的“顶帖”）；不同账号发同一段文案，基本是批量运营。
    """
    sh = [
        _shingles(p.get("desc") or "") if len(p.get("desc") or "") >= 40 else set() for p in posts
    ]
    out: dict[str, list[str]] = {}
    for i, a in enumerate(posts):
        for j in range(i + 1, len(posts)):
            b = posts[j]
            ua, ub = a.get("user", {}).get("userId"), b.get("user", {}).get("userId")
            if not sh[i] or not sh[j] or (ua and ua == ub):
                continue
            if len(sh[i] & sh[j]) / len(sh[i] | sh[j]) >= threshold:
                out.setdefault(a["id"], []).append(b.get("user", {}).get("nickname", "?"))
                out.setdefault(b["id"], []).append(a.get("user", {}).get("nickname", "?"))
    return out


def ensure_bridge(bridge_url: str) -> dict[str, bool]:
    """检查 bridge server 与扩展连接状态（不负责启动）。"""
    from xhs.bridge import BridgePage

    page = BridgePage(bridge_url)
    server = page.is_server_running()
    ext = server and page.is_extension_connected()
    return {"server": bool(server), "extension": bool(ext)}


def summarize_paths(work: Path) -> dict[str, str]:
    names = [
        "raw_feeds.json",
        "filtered_feeds.json",
        "detailed_posts.json",
        "analyzed_results.json",
        "review.md",
    ]
    return {n: str(work / n) for n in names if (work / n).exists()}


__all__ = [
    "BridgeUnavailableError",
    "RentalPipeline",
    "VerificationRequiredError",
    "ensure_bridge",
    "summarize_paths",
]
