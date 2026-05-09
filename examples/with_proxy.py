"""Same as basic.py but route through a SOCKS5 proxy."""
import os

from stealthfox import Stealthfox


def main() -> None:
    proxy = {
        "server": os.environ.get("STEALTHFOX_PROXY_SERVER", "socks5://127.0.0.1:1080"),
    }
    user = os.environ.get("STEALTHFOX_PROXY_USER")
    password = os.environ.get("STEALTHFOX_PROXY_PASS")
    if user and password:
        proxy["username"] = user
        proxy["password"] = password

    with Stealthfox(proxy=proxy) as browser:
        page = browser.new_page()
        page.goto("https://httpbin.org/ip")
        print(page.content()[:500])


if __name__ == "__main__":
    main()
