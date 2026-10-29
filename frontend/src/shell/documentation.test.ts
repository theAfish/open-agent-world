import { describe, expect, it } from 'vitest';
import catalog from '../../../docs/documentation.json';
import { documentation, documentationAssets, documentationGroups, documentationLink, localizedPage, documentIdentity, resolveDocumentationPage, documentationLanguages, remarkDocumentationHeadings, headingSlug } from './documentation';
import { DOCS_URL } from './helpChecks';

describe('bundled manual', () => {
  it('includes every published navigation page and local image', () => {
    const published = catalog.nav.flatMap(group => Object.values(group).flat());
    const bundledTopics = [...new Set(Object.keys(documentation).map(path => documentIdentity(path).topic))];
    expect(bundledTopics.sort()).toEqual(published.sort());
    for (const path of published) expect(documentation[path], path).toBeDefined();
    for (const [page, { markdown }] of Object.entries(documentation)) {
      for (const match of markdown.matchAll(/!\[[^\]]*\]\(([^)]+)\)/g)) {
        expect(documentationLink(match[1], page).kind, `${page}: ${match[1]}`).toBe('asset');
      }
    }
    expect(documentationAssets['assets/demos/connect-cards.gif']).toBeTruthy();
    expect(documentation['README.md'].markdown).not.toMatch(/hide:|<div|\.md-button/);
    expect(documentation['node-effects.md'].markdown).toContain('<div style={{');
    expect(Object.keys(documentation).some(path => path.startsWith('internal/'))).toBe(false);
  });

  it('lists every topic once per language and falls back to available source text', () => {
    for (const { locale } of documentationLanguages) {
      const pages = documentationGroups(locale).flatMap(group => group.pages);
      expect(new Set(pages).size).toBe(pages.length);
      for (const page of Object.keys(documentation)) expect(pages).toContain(localizedPage(page, locale));
      for (const page of pages) expect(documentation[page]).toBeDefined();
    }
    expect(localizedPage('user-guide/index.md', 'zh-CN')).toBe('user-guide/index.zh-CN.md');
    expect(localizedPage('user-guide/models.md', 'zh-CN')).toBe('user-guide/models.zh-CN.md');
    expect(documentationGroups('zh-CN').find(group => group.title === 'Contribute to OAW')?.pages)
      .toContain('contributing/hosting.zh-CN.md');
  });

  it('uses the same audience for every website and offline topic, including translations', () => {
    const canonical = (path: string) => documentIdentity(path).topic;
    const website = new Map(catalog.nav.map(group => Object.entries(group)[0]));
    for (const { locale } of documentationLanguages) {
      const groups = documentationGroups(locale);
      expect(groups.map(group => group.title)).toEqual([...website.keys()]);
      for (const group of groups) {
        expect(group.pages.map(canonical).sort(), `${locale}: ${group.title}`)
          .toEqual([...new Set(website.get(group.title))].sort());
      }
    }
  });

  it('keeps relative pages, published URLs, translated pages and anchors local', () => {
    expect(documentationLink('../install.md#updates-and-troubleshooting', 'user-guide/index.md'))
      .toEqual({ kind: 'page', page: 'install.md', hash: 'updates-and-troubleshooting' });
    expect(documentationLink('#connect-a-model', 'user-guide/models.md'))
      .toEqual({ kind: 'page', page: 'user-guide/models.md', hash: 'connect-a-model' });
    expect(documentationLink(`${DOCS_URL}user-guide/`, 'README.md'))
      .toEqual({ kind: 'page', page: 'user-guide/index.md', hash: '' });
    expect(documentationLink(`${DOCS_URL}README.zh-CN/`, 'README.md'))
      .toEqual({ kind: 'page', page: 'README.zh-CN.md', hash: '' });
    expect(documentationLink('../../backend/plugins/registry.py', 'developers/first-plugin.md'))
      .toEqual({ kind: 'external', url: 'https://github.com/theAfish/open-agent-world/blob/dev/backend/plugins/registry.py' });
    expect(documentationLink('javascript:alert(1)', 'README.md').kind).toBe('unavailable');
    expect(documentationLink('file:///secret', 'README.md').kind).toBe('unavailable');
  });
});

it('resolves a third, partially translated language without special cases', () => {
  const languages = [{ locale: 'en' }, { locale: 'zh-CN' }, { locale: 'ja' }, { locale: 'pt-BR' }];
  const pages = ['guide.md', 'guide.zh-CN.md', 'guide.ja.md', 'api.md', 'guide.pt-BR.md'];
  expect(resolveDocumentationPage('guide.zh-CN.md', 'ja', pages, languages)).toBe('guide.ja.md');
  expect(resolveDocumentationPage('api.md', 'ja', pages, languages)).toBe('api.md');
  expect(resolveDocumentationPage('guide.ja.md', 'pt-BR', pages, languages)).toBe('guide.pt-BR.md');
  expect(resolveDocumentationPage('guide.ja.md', 'removed-language', pages, languages)).toBe('guide.md');
});

it('follows the reading language while preserving explicit source anchors', () => {
  expect(documentationLink('../install.md', 'user-guide/index.zh-CN.md', 'zh-CN'))
    .toEqual({ kind: 'page', page: 'install.zh-CN.md', hash: '' });
  expect(documentationLink(`${DOCS_URL}zh-CN/user-guide/`, 'README.md'))
    .toEqual({ kind: 'page', page: 'user-guide/index.zh-CN.md', hash: '' });
  expect(documentationLink(`${DOCS_URL}zh-CN/`, 'README.md'))
    .toEqual({ kind: 'page', page: 'README.zh-CN.md', hash: '' });
  expect(documentationLink('card-app.md#4-add-the-react-interface', 'developers/frontend.md', 'zh-CN'))
    .toEqual({ kind: 'page', page: 'developers/card-app.md', hash: '4-add-the-react-interface' });
});

it('keeps non-Latin heading anchors compatible with published and legacy URLs', () => {
  expect(headingSlug('Déjà vu — Hello')).toBe('deja-vu-hello');
  const headings = ['下载与安装', '第一次使用', 'ガイド', 'Windows'].map(value => ({
    type: 'heading', children: [{ type: 'text', value }], data: { hProperties: { id: '' } },
  }));
  remarkDocumentationHeadings()({ type: 'root', children: headings });
  expect(headings.map(node => node.data.hProperties.id)).toEqual(['_1', '_2', '_3', 'windows']);
  expect(documentationLink(`${DOCS_URL}zh-CN/install/#_1`, 'README.md', 'en'))
    .toEqual({ kind: 'page', page: 'install.zh-CN.md', hash: '_1' });
});
