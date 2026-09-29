"""Small, backend-independent checks for documentation publishing behavior."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main

import markdown
from mkdocs.exceptions import PluginError

from docs_hooks import RepositoryLinksExtension
from docs import validate_source, navigation_pages


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
        self.extension.repository_paths = {'docs/guide.md'}

    def render(self, source):
        return markdown.markdown(source, extensions=[self.extension, "fenced_code"])

    def test_source_links_use_github_and_keep_fragments(self):
        (self.root / "example file.py").write_text("pass", encoding="utf-8")
        self.extension.repository_paths.add('example file.py')
        html = self.render("[source](../example%20file.py#L1)")
        self.assertIn('href="https://github.com/example/project/blob/dev/example%20file.py#L1"', html)

    def test_published_pages_stay_relative(self):
        self.assertIn('href="guide.md#guide"', self.render("[guide](guide.md#guide)"))

    def test_excluded_design_notes_link_to_repository(self):
        (self.docs / "proposal.md").write_text("# Draft", encoding="utf-8")
        self.extension.repository_paths.add('docs/proposal.md')
        self.assertIn('/blob/dev/docs/proposal.md', self.render("[draft](proposal.md)"))

    def test_missing_repository_target_fails_instead_of_hiding_broken_link(self):
        with self.assertRaises(PluginError):
            self.render("[missing](../missing.py)")

    def test_code_examples_are_not_rewritten(self):
        html = self.render("```md\n[example](../not-a-file.py)\n```")
        self.assertIn("[example](../not-a-file.py)", html)

    def test_existing_local_output_is_rejected(self):
        (self.root / '.outputs').mkdir()
        (self.root / '.outputs/report.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(PluginError, 'ignored or has incorrect case'):
            self.render('[local report](../.outputs/report.json)')

    def test_case_mismatch_is_rejected_even_on_windows(self):
        self.extension.repository_paths = {'docs/Guide.md'}
        with self.assertRaisesRegex(PluginError, 'incorrect case'):
            self.render('[guide](guide.md)')

    def test_link_cannot_escape_repository(self):
        with self.assertRaisesRegex(PluginError, 'leaves repository'):
            self.render('[outside](../../outside.md)')

    def test_published_image_stays_local_and_checks_case(self):
        asset = self.docs / 'diagram.svg'
        asset.write_text('<svg/>', encoding='utf-8')
        self.extension.published.add(asset.resolve())
        self.extension.repository_paths.add('docs/diagram.svg')
        self.assertIn('src="diagram.svg"', self.render('![Diagram](diagram.svg)'))
        self.extension.repository_paths.remove('docs/diagram.svg')
        with self.assertRaisesRegex(PluginError, 'incorrect case'):
            self.render('![Diagram](diagram.svg)')

    def test_external_and_fragment_links_are_unchanged(self):
        html = self.render("[web](https://example.org/a) [section](#guide)")
        self.assertIn('href="https://example.org/a"', html)
        self.assertIn('href="#guide"', html)


class SourcePolicyTest(TestCase):
    def test_examples_do_not_count_as_prose_or_headings(self):
        self.assertEqual(validate_source('guide.md',
            '# Guide\n\n```md\n# 中文示例\n```\n\n## Result\n'), [])

    def test_language_switch_and_inline_identifier_are_allowed(self):
        self.assertEqual(validate_source('guide.md',
            '# Guide\n[简体中文](guide.zh-CN.md)\nUse `中文标识`.\n'), [])

    def test_mixed_prose_reports_source_line(self):
        self.assertIn('guide.md:3:', validate_source('guide.md', '# Guide\n\n中文正文\n')[0])

    def test_chinese_translation_is_allowed(self):
        self.assertEqual(validate_source('guide.zh-CN.md', '# 指南\n\n中文正文\n'), [])

    def test_duplicate_title_and_skipped_heading_fail(self):
        errors = validate_source('guide.md', '# Guide\n### Skipped\n# Second\n')
        self.assertEqual(len(errors), 2)

    def test_navigation_includes_nested_and_index_pages(self):
        self.assertEqual(list(navigation_pages([
            {'Guide': ['user-guide/index.md', {'Install': 'install.md'}]},
        ])), ['user-guide/index.md', 'install.md'])


if __name__ == "__main__":
    main()
