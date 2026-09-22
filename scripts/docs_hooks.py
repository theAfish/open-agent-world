"""Keep repository-relative source links useful on the published docs site.

Only links outside the published docs are sent to GitHub. MkDocs validates
ordinary page/asset links and anchors; missing repository targets fail builds.
"""

from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor
from mkdocs.exceptions import PluginError


class RepositoryLinks(Treeprocessor):
    def __init__(self, extension):
        super().__init__()
        self.extension = extension

    def run(self, root):
        extension = self.extension
        for element in root.iter("a"):
            href = element.get("href", "")
            url = urlsplit(href)
            if url.scheme or url.netloc or not url.path or url.path.startswith("/"):
                continue
            target = (extension.page_path.parent / unquote(url.path)).resolve()
            if not target.is_relative_to(extension.repo_root):
                raise PluginError(f"Documentation link leaves repository: {href}")
            if target in extension.published:
                continue
            if not target.exists():
                raise PluginError(f"Missing repository link in {extension.page_path}: {href}")
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
    config.markdown_extensions.append(extension)
    return config


def on_page_markdown(markdown, *, page, config, files):
    # Keep general search focused on tasks and tutorials. Detailed contracts stay
    # available from the reference navigation and the developer topic directory.
    if page.file.src_uri not in {"README.md", "README.zh-CN.md", "install.md"} and not page.file.src_uri.startswith(("user-guide/", "developers/")):
        page.meta["search"] = {"exclude": True}
    extension = next(item for item in config.markdown_extensions if isinstance(item, RepositoryLinksExtension))
    extension.page_path = Path(page.file.abs_src_path).resolve()
    extension.published = {
        Path(file.abs_src_path).resolve()
        for file in files
        if file.abs_src_path and file.inclusion.is_included()
    }
    return markdown
