# 维护与翻译文档

网站和应用内帮助共用 `docs/` 中的 Markdown。插件 README 负责各自的安装与使用说明；
`docs/internal/` 不进入公开网站或应用帮助。写作要求见[文档检查表](../contributing/docs-checklist.md)。

## 目录与语言

每个主题只在 `docs/documentation.json` 的对应读者分类中登记一次，使用英文源文件名。
`mkdocs.yml` 通过 `INHERIT` 读取这份配置，应用直接导入它。无需另维护一份应用目录，
也不要为每种译文重复添加导航项。

译文与源文放在一起，例如 `guide.md`、`guide.zh-CN.md`、`guide.ja.md`。
不带语言后缀的是默认英文源文，每个主题都需要有它。语言代码采用语言标签，例如 `zh-CN`、
`ja`、`pt-BR`，不用国旗代替语言。网站英文地址仍为 `/guide/`，中文为 `/zh-CN/guide/`；
旧的带 `.zh-CN` 地址会跳转，保留章节锚点。

网站页头和应用文档工具栏统一提供语言切换，正文标题下不再放切换链接。
应用默认跟随界面语言，也允许单独选择文档语言并保存到当前工作区。
缺少译文时显示英文原文及提示；继续浏览时仍保留所选阅读语言。

## 增加译文或语言

已有语言只需增加对应文件，例如 `user-guide/models.zh-CN.md`。
增加一种新语言时，在 `docs/documentation.json` 的 `plugins.i18n.languages` 中复制一条非默认语言配置：

1. 修改 `locale`（例如 `ja`）、语言自称 `name`（例如 `日本語`），保留 `default: false`，设置 `build: true`。
   `theme.language` 使用 Material 支持的语言代码。
2. 翻译 `extra.documentation` 的四条提示，以及 `nav_translations` 的四个读者分类。
   保留 `{available}` 等占位符。
3. 优先翻译首页、用户入门和 Card App 教程。允许部分翻译；不要把英文复制到译文文件中来隐藏缺译提示。
   从右到左书写的语言设置 `theme.direction: rtl`；应用文章使用自动文字方向。
4. 执行下方检查，并查看网站和应用内帮助。插件会自动添加支持的搜索语言；
   区域语言标签需要核对 Lunr 支持的代码，必要时补充 `plugins.search.lang`。

翻译应保留正确的命令、API 标识符和可见 UI 名称。产品事实改变时，同步更新已有译文，
或在评审中明确列出待更新文件。存在译文文件不代表它已经跟上当前版本；仓库不自动翻译或认证翻译质量。

## 链接

普通主题链接使用不带语言后缀的路径，例如 `../install.md`，阅读器会选择相应译文。
链接到具体章节时，使用该源文件及真实锚点；带锚点的链接保留所引用的源文，不猜测译后标题。

源码链接使用大小写准确的仓库相对路径。发布后指向 GitHub `dev`，应用会标注需要联网；
文档页面和图片保持离线可读。新增内容在构建时打入应用，网站更新不会改写已安装版本的手册。
不要引用本机日志或忽略目录中的产物。

## 预览与检查

在仓库根目录使用装好文档依赖的 Python 环境：

```sh
python -m pip install -r scripts/docs-requirements.txt
python scripts/docs.py serve
python scripts/docs.py check
```

检查覆盖主题与译文清单、标题、语言分离、仓库链接、严格网站构建、生成后的锚点和搜索范围。
不需要启动后端。修改导航或 Help 行为后，在 `frontend` 中执行：

```sh
npx vitest run src/shell/documentation.test.ts src/shell/DocumentationPanel.test.tsx src/shell/HelpMenu.test.tsx
npm run build
```

准备好测试宿主后，运行 `node scripts/run-e2e.mjs e2e/documentation.spec.ts`，
检查离线阅读、弹窗焦点、图片、语言切换和窄屏布局。

文档工作流检查 PR 和分支推送，`dev` 的 Pages 发布与桌面发行分开进行。
网站跟随开发分支；发行版用户应以随应用提供的手册和发行说明为准。
