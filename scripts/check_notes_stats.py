"""定时巡检小红书笔记数据与互动情况"""

import json
import sys
import time

sys.path.insert(0, r"C:\Users\LENOVO\.gemini\config\skills\xiaohongshu-skills\scripts")
from cli import _ensure_bridge_ready
from xhs.bridge import BridgePage


def get_notes_overview() -> dict:
    _ensure_bridge_ready("ws://localhost:9333")
    page = BridgePage("ws://localhost:9333")
    page.navigate("https://creator.xiaohongshu.com/new/note-manager")
    time.sleep(3)
    page.wait_dom_stable()

    js = """
    (() => {
        const cards = Array.from(document.querySelectorAll('.note-card'));
        const results = [];
        for (const card of cards) {
            const lines = card.innerText.split('\\n').map(s => s.trim()).filter(Boolean);
            const title = lines[0] || '未知标题';
            const publishTime = lines[1] || '';
            const numbers = lines.slice(2).filter(s => /^\\d+$/.test(s));
            
            const coverEl = card.querySelector('.note-card__cover img');
            results.push({
                title: title,
                publishTime: publishTime,
                cover: coverEl ? coverEl.src : null,
                metrics: {
                    views: numbers[0] || '0',
                    likes: numbers[1] || '0',
                    collections: numbers[2] || '0',
                    comments: numbers[3] || '0',
                    shares: numbers[4] || '0'
                }
            });
        }
        return {
            timestamp: new Date().toISOString(),
            total_notes: results.length,
            notes: results
        };
    })()
    """
    return page.evaluate(js)


if __name__ == "__main__":
    data = get_notes_overview()
    print(json.dumps(data, ensure_ascii=False, indent=2))
