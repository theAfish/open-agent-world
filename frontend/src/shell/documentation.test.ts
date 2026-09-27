import { describe, expect, it } from 'vitest';
import siteConfig from '../../../mkdocs.yml?raw';
import { documentation, documentationAssets, documentationGroups, documentationLink, localizedPage } from './documentation';
import { DOCS_URL } from './helpChecks';

describe('bundled manual', () => {
  it('includes every published navigation page and local image', () => {
    const nav = siteConfig.slice(siteConfig.indexOf('\nnav:'));
    for (const match of nav.matchAll(/(?:^|\s)([\w./-]+\.md)\s*$/gm)) {
      expect(documentation[match[1]], match[1]).toBeDefined();
    }
    for (const [page, { markdown }] of Object.entries(documentation)) {
      for (const match of markdown.matchAll(/!\[[^\]]*\]\(([^)]+)\)/g)) {
        expect(documentationLink(match[1], page).kind, `${page}: ${match[1]}`).toBe('asset');
      }
    }
    expect(documentationAssets['assets/demos/connect-cards.gif']).toBeTruthy();
    expect(documentation['README.md'].markdown).not.toMatch(/hide:|<div|\.md-button/);
    expect(documentation['node-effects.md'].markdown).toContain('<div style={{');
    expect(documentation['SHADOW_COLLECTION.md']).toBeUndefined();
  });

  it('lists every topic once per language and falls back to available source text', () => {
    for (const locale of ['en', 'zh-CN'] as const) {
      const pages = documentationGroups(locale).flatMap(group => group.pages);
      expect(new Set(pages).size).toBe(pages.length);
      for (const page of Object.keys(documentation)) expect(pages).toContain(localizedPage(page, locale));
      for (const page of pages) expect(documentation[page]).toBeDefined();
    }
    expect(localizedPage('user-guide/index.md', 'zh-CN')).toBe('user-guide/index.zh-CN.md');
    expect(localizedPage('user-guide/models.md', 'zh-CN')).toBe('user-guide/models.md');
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
