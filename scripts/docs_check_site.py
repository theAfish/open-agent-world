"""Check the built site's local links and its reader-facing search boundary."""

import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import yaml
from mkdocs.structure.files import File
from docs import navigation_pages


class Page(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.ids = set()
        self.links = []
        self.languages = set()
        self.content_language = None
        self.feed(source)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'a' and 'md-select__link' in attrs.get('class', ''):
            self.languages.add(attrs.get('hreflang'))
        if tag == 'div' and attrs.get('lang'):
            self.content_language = attrs['lang']
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        attribute = "href" if tag in {"a", "link"} else "src"
        if attrs.get(attribute):
            self.links.append(attrs[attribute])


def check(site=Path(".tmp/docs-site")):
    config = yaml.safe_load(Path("mkdocs.yml").read_text(encoding="utf-8"))
    catalog = json.loads(Path('docs/documentation.json').read_text(encoding='utf-8'))
    languages = catalog['plugins']['i18n']['languages']
    locales = [item['locale'] for item in languages if item['build'] and not item['default']]
    prefix = urlsplit(config["site_url"]).path.rstrip("/") + "/"
    pages = {path.relative_to(site).as_posix(): Page(path.read_text(encoding="utf-8")) for path in site.rglob("*.html")}
    errors = []
    count = 0
    built_languages = {item['locale'] for item in languages if item['build']}
    default = next(item['locale'] for item in languages if item['default'])
    for topic in navigation_pages(catalog['nav']):
        destination = File(topic, 'docs', str(site), True).dest_uri
        for locale in built_languages:
            path = (locale + '/' if locale != default else '') + destination
            page = pages.get(path)
            translated = Path('docs') / topic.replace('.md', f'.{locale}.md')
            expected = locale if translated.exists() else default
            if page is None or page.content_language != expected:
                errors.append(f'{path}: missing topic or incorrect content language (expected {expected})')
            if len(built_languages) > 1 and (page is None or page.languages != built_languages):
                errors.append(f'{path}: incomplete header language selector')
            if locale != default and translated.exists():
                old = File(translated.relative_to('docs').as_posix(), 'docs', str(site), True).dest_uri
                alias = site / old
                if not alias.exists() or 'location.search + location.hash' not in alias.read_text(encoding='utf-8'):
                    errors.append(f'{old}: missing redirect with fragment preservation')
    for path, page in pages.items():
        for link in page.links:
            url = urlsplit(urljoin(f"https://docs.invalid{prefix}{path}", link))
            if url.netloc not in {"docs.invalid", urlsplit(config["site_url"]).netloc} or url.scheme not in {"http", "https"}:
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
        for locale in locales:
            if location.startswith(locale + '/'):
                location = location[len(locale) + 1:]
                break
        if location not in {"", "install/"} and not location.startswith(("user-guide/", "developers/", "contributing/")):
            errors.append(f"Internal reference leaked into general search: {location}")
    for required in ("user-guide/first-team/", "developers/first-plugin/", "zh-CN/user-guide/", "zh-CN/install/", "contributing/docs-checklist/"):
        if required not in locations:
            errors.append(f"Missing guide from search: {required}")
    for excluded in ("internal",):
        if (site / excluded).exists():
            errors.append(f"Design draft was published: {excluded}")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Checked {len(pages)} pages, {count} local links/assets, and {len(locations)} search pages.")


if __name__ == "__main__":
    check()
