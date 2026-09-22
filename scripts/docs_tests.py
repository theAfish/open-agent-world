"""Small, backend-independent checks for documentation publishing behavior."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main

import markdown
from mkdocs.exceptions import PluginError

from docs_hooks import RepositoryLinksExtension


class RepositoryLinksTest(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.docs = self.root / "docs"
        self.docs.mkdir()
        self.page = self.docs / "guide.md"
        self.page.write_text("# Guide", encoding="utf-8")
        self.extension = RepositoryLinksExtension()
        self.extension.repo_root = self.root.resolve()
        self.extension.repo_url = "https://github.com/example/project"
        self.extension.branch = "dev"
        self.extension.page_path = self.page.resolve()
        self.extension.published = {self.page.resolve()}

    def render(self, source):
        return markdown.markdown(source, extensions=[self.extension, "fenced_code"])

    def test_source_links_use_github_and_keep_fragments(self):
        (self.root / "example file.py").write_text("pass", encoding="utf-8")
        html = self.render("[source](../example%20file.py#L1)")
        self.assertIn('href="https://github.com/example/project/blob/dev/example%20file.py#L1"', html)

    def test_published_pages_stay_relative(self):
        self.assertIn('href="guide.md#guide"', self.render("[guide](guide.md#guide)"))

    def test_excluded_design_notes_link_to_repository(self):
        (self.docs / "proposal.md").write_text("# Draft", encoding="utf-8")
        self.assertIn('/blob/dev/docs/proposal.md', self.render("[draft](proposal.md)"))

    def test_missing_repository_target_fails_instead_of_hiding_broken_link(self):
        with self.assertRaises(PluginError):
            self.render("[missing](../missing.py)")

    def test_code_examples_are_not_rewritten(self):
        html = self.render("```md\n[example](../not-a-file.py)\n```")
        self.assertIn("[example](../not-a-file.py)", html)

    def test_external_and_fragment_links_are_unchanged(self):
        html = self.render("[web](https://example.org/a) [section](#guide)")
        self.assertIn('href="https://example.org/a"', html)
        self.assertIn('href="#guide"', html)


if __name__ == "__main__":
    main()
