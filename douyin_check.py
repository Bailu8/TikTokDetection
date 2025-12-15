import argparse
from urllib.parse import quote_plus

import requests


DOUYIN_CHECK_URL_TEMPLATE = "https://link.wtturl.cn/?aid=1128&lang=zh&scene=im&jumper_version=1&target={target}"
WEIBO_CHECK_URL_TEMPLATE = "https://weibo.cn/sinaurl?u={target}"

# 抖音：命中这些关键字视为拦截状态
DOUYIN_BLOCK_KEYWORDS = [
    "第三方网页",
    "停止",
    "已终止访问该网页",
]


def check_douyin_jump(target_url: str, timeout: int = 10) -> dict:
    """使用抖音检测链接检查目标地址是否被拦截。

    返回结果示例：
    - {"status": "blocked", "keywords": [...], "http_status": 200}
    - {"status": "ok", "redirect_to": "https://...", "http_status": 302}
    - {"status": "unknown", "http_status": 200}
    - {"status": "error", "error": "..."}
    """
    encoded_target = quote_plus(target_url)
    check_url = DOUYIN_CHECK_URL_TEMPLATE.format(target=encoded_target)

    try:
        # 不自动跟随重定向，方便判断是否正常跳转
        resp = requests.get(check_url, timeout=timeout, allow_redirects=False)
    except requests.RequestException as exc:  # 网络/超时等错误
        return {"status": "error", "error": str(exc)}

    status_code = resp.status_code

    # 如果返回 3xx 并且包含 Location，一般表示正常跳转
    if 300 <= status_code < 400 and "Location" in resp.headers:
        return {
            "status": "ok",
            "redirect_to": resp.headers["Location"],
            "http_status": status_code,
        }

    text = resp.text or ""
    hit_keywords = [kw for kw in DOUYIN_BLOCK_KEYWORDS if kw in text]

    if hit_keywords:
        return {
            "status": "blocked",
            "keywords": hit_keywords,
            "http_status": status_code,
        }

    # 没有命中关键字、也没有明显的 3xx 跳转，标记为未知状态
    return {
        "status": "unknown",
        "http_status": status_code,
    }


def check_weibo_jump(target_url: str, timeout: int = 10) -> dict:
    """使用微博 sinaurl 检测目标地址是否被拦截。

    规则：
    - 若返回 3xx 且带 Location，视为正常跳转（"ok"）。
    - 若页面文案中包含“将要访问”或“已停止访问”，视为拦截（"blocked"）。
    - 其余情况为 "unknown" 或 "error"。
    """

    encoded_target = quote_plus(target_url)
    check_url = WEIBO_CHECK_URL_TEMPLATE.format(target=encoded_target)

    try:
        resp = requests.get(check_url, timeout=timeout, allow_redirects=False)
    except requests.RequestException as exc:
        return {"status": "error", "error": str(exc)}

    status_code = resp.status_code

    if 300 <= status_code < 400 and "Location" in resp.headers:
        return {
            "status": "ok",
            "redirect_to": resp.headers["Location"],
            "http_status": status_code,
        }

    text = resp.text or ""
    weibo_block_keywords = ["将要访问", "已停止访问"]
    hit_keywords = [kw for kw in weibo_block_keywords if kw in text]

    if hit_keywords:
        return {
            "status": "blocked",
            "keywords": hit_keywords,
            "http_status": status_code,
        }

    return {
        "status": "unknown",
        "http_status": status_code,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="抖音跳转检测脚本")
    parser.add_argument(
        "url",
        help="需要检测的目标网址（会作为 target 参数填入抖音检测链接）",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="请求超时时间（秒），默认 10",
    )

    args = parser.parse_args()

    result = check_douyin_jump(args.url, timeout=args.timeout)

    status = result.get("status")

    # 输出尽量简单，只关心整体结果
    if status == "ok":
        print("检测结果：正常")
    elif status == "blocked":
        print("检测结果：拦截")
    elif status == "unknown":
        print("检测结果：未知")
    else:  # error
        print("检测结果：错误")


if __name__ == "__main__":
    main()
