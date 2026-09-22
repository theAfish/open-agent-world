"""Check the built site's local links and its reader-facing search boundary."""

import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import yaml


class Page(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.ids = set()
        self.links = []
        self.feed(source)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        attribute = "href" if tag in {"a", "link"} else "src"
        if attrs.get(attribute):
            self.links.append(attrs[attribute])


def check(site=Path(".tmp/docs-site")):
    config = yaml.safe_load(Path("mkdocs.yml").read_text(encoding="utf-8"))
    prefix = urlsplit(config["site_url"]).path.rstrip("/") + "/"
    pages = {path.relative_to(site).as_posix(): Page(path.read_text(encoding="utf-8")) for path in site.rglob("*.html")}
    errors = []
    count = 0
    for path, page in pages.items():
        for link in page.links:
            url = urlsplit(urljoin(f"https://docs.invalid{prefix}{path}", link))
            if url.netloc != "docs.invalid" or url.scheme not in {"http", "https"}:
                continue
            if not url.path.startswith(prefix):
                errors.append(f"{path}: link escapes project URL prefix: {link}")
                continue
            target = unquote(url.path[len(prefix):])
            if not target or target.endswith("/"):
                target += "index.html"
            if not (site / target).is_file():
                errors.append(f"{path}: missing {link}")
            elif url.fragment and target in pages and unquote(url.fragment) not in pages[target].ids:
                errors.append(f"{path}: missing anchor {link}")
            count += 1
    search = json.loads((site / "search/search_index.json").read_text(encoding="utf-8"))
    locations = {entry["location"].split("#")[0] for entry in search["docs"]}
    for location in locations:
        if location not in {"", "README.zh-CN/", "install/"} and not location.startswith(("user-guide/", "developers/")):
            errors.append(f"Internal reference leaked into general search: {location}")
    for required in ("user-guide/first-team/", "developers/first-plugin/", "user-guide/index.zh-CN/"):
        if required not in locations:
            errors.append(f"Missing guide from search: {required}")
    for excluded in ("SHADOW_COLLECTION", "SHADOW_GAS_BOUNDARY", "READER_TRANSITION", "matcreator-demo-plan"):
        if (site / excluded).exists():
            errors.append(f"Design draft was published: {excluded}")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Checked {len(pages)} pages, {count} local links/assets, and {len(locations)} search pages.")


if __name__ == "__main__":
    check()
