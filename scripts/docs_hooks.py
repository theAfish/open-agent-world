"""Keep repository-relative source links useful on the published docs site.

Only links outside the published docs are sent to GitHub. MkDocs validates
ordinary page/asset links and anchors; missing repository targets fail builds.
"""

from pathlib import Path
from html import escape
import json
import subprocess
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor
from mkdocs.exceptions import PluginError
from mkdocs.plugins import event_priority
from mkdocs.structure.files import File, get_files


def localized_url(source, config):
    """Destination of an explicit source, including its language and section IDs."""
    languages = config.plugins['i18n'].config.languages
    default = next(item.locale for item in languages if item.default)
    locale = next((item.locale for item in languages if source.endswith(f'.{item.locale}.md')), default)
    canonical = source.removesuffix(f'.{locale}.md') + '.md' if source.endswith(f'.{locale}.md') else source
    url = File(canonical, config.docs_dir, config.site_dir, config.use_directory_urls).url
    return (f'{locale}/' if locale != default else '') + ('' if url == './' else url)


class RepositoryLinks(Treeprocessor):
    def __init__(self, extension):
        super().__init__()
        self.extension = extension

    def run(self, root):
        extension = self.extension
        for element in root.iter():
            if element.tag not in {'a', 'img'}:
                continue
            attribute = 'href' if element.tag == 'a' else 'src'
            href = element.get(attribute, "")
            url = urlsplit(href)
            if url.scheme or url.netloc or not url.path or url.path.startswith("/"):
                continue
            target = (extension.page_path.parent / unquote(url.path)).resolve()
            if not target.is_relative_to(extension.repo_root):
                raise PluginError(f"Documentation link leaves repository: {href}")
            if not target.exists():
                raise PluginError(f"Missing repository link in {extension.page_path}: {href}")
            relative = target.relative_to(extension.repo_root).as_posix()
            # Exact strings catch Windows-only case mismatches. The inventory
            # excludes ignored local artifacts even when they exist on this PC.
            if relative not in extension.repository_paths:
                raise PluginError(f"Repository link is ignored or has incorrect case in {extension.page_path}: {href}")
            if target in extension.published:
                if url.fragment and target.suffix == '.md' and hasattr(extension, 'site_config'):
                    source = target.relative_to(extension.site_config.docs_dir).as_posix()
                    selected = extension.files.get_file_from_path(source)
                    # A translated heading has a different ID. Keep explicit deep
                    # links attached to their actual source instead of guessing.
                    if selected is None or Path(selected.abs_src_path).resolve() != target:
                        prefix = urlsplit(extension.site_config.site_url).path.rstrip('/') + '/'
                        destination = prefix + localized_url(source, extension.site_config)
                        element.set('href', urlunsplit(('', '', destination, url.query, url.fragment)))
                continue
            if element.tag == 'img':
                raise PluginError(f"Image must be a published documentation asset: {href}")
            kind = "tree" if target.is_dir() else "blob"
            path = quote(target.relative_to(extension.repo_root).as_posix(), safe="/")
            github_path = f"{extension.repo_url}/{kind}/{extension.branch}/{path}"
            element.set("href", urlunsplit((*urlsplit(github_path)[:3], url.query, url.fragment)))


class RepositoryLinksExtension(Extension):
    def extendMarkdown(self, md):
        # Resolve links after Markdown inline parsing, before MkDocs rewrites paths.
        md.treeprocessors.register(RepositoryLinks(self), "repository-links", 15)


def on_config(config):
    extension = RepositoryLinksExtension()
    extension.repo_root = Path(config.config_file_path).resolve().parent
    extension.repo_url = config.repo_url.rstrip("/")
    extension.branch = "dev"
    inventory = subprocess.run(
        ['git', '-c', f'safe.directory={extension.repo_root.as_posix()}',
         'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
        cwd=extension.repo_root, check=True, capture_output=True,
    ).stdout.decode('utf-8').split('\0')
    extension.repository_paths = {path for path in inventory if path}
    for path in tuple(extension.repository_paths):
        extension.repository_paths.update(parent.as_posix() for parent in Path(path).parents)
    config.markdown_extensions = [item for item in config.markdown_extensions if not isinstance(item, RepositoryLinksExtension)]
    config.markdown_extensions.append(extension)
    return config


def on_page_markdown(markdown, *, page, config, files):
    # Keep general search focused on tasks and tutorials. Detailed contracts stay
    # available from the reference navigation and the developer topic directory.
    topic = getattr(page.file, "norm_src_uri", page.file.src_uri)
    if topic not in {"README.md", "install.md"} and not topic.startswith(("user-guide/", "developers/", "contributing/")):
        page.meta["search"] = {"exclude": True}
    extension = next(item for item in config.markdown_extensions if isinstance(item, RepositoryLinksExtension))
    extension.page_path = Path(page.file.abs_src_path).resolve()
    extension.site_config = config
    extension.files = files
    extension.published = {
        Path(file.abs_src_path).resolve()
        for file in get_files(config)
        if file.abs_src_path and file.inclusion.is_included()
    }
    return markdown


@event_priority(-200)
def on_post_build(config):
    """Preserve pre-i18n public translation URLs, including inbound fragments."""
    for file in get_files(config).documentation_pages():
        if not file.inclusion.is_included():
            continue
        if not any(file.src_uri.endswith(f'.{language.locale}.md')
                   for language in config.plugins['i18n'].config.languages if not language.default):
            continue
        destination = localized_url(file.src_uri, config)
        if destination == file.url:
            continue
        target = Path(config.site_dir) / file.dest_uri
        target.parent.mkdir(parents=True, exist_ok=True)
        prefix = urlsplit(config.site_url).path.rstrip('/') + '/'
        href = prefix + destination
        target.write_text(
            '<!doctype html><html><head><meta charset="utf-8">'
            f'<link rel="canonical" href="{escape(href, quote=True)}">'
            f'<meta http-equiv="refresh" content="0; url={escape(href, quote=True)}">'
            '<title>Redirect</title></head><body>'
            f'<a href="{escape(href, quote=True)}">Continue</a>'
            f'<script>location.replace({json.dumps(href)} + location.search + location.hash)</script>'
            '</body></html>', encoding='utf-8',
        )
