# 卡片工厂

Card Factory 提供可视化卡片制作与打包。在 **Library → Packs** 开启工厂卡包，把工作台加入牌组后放到画布；也可分别放置并连接设备。

## 设计并打印

1. **Pack Designer** 设置卡包 ID、名称、版本、作者、说明和包装外观。
2. **Face Designer** 选择布局、样式、内容和表面效果。先使用基础控制即可；高级模式提供图层、形状、约束和样式设置。
3. **Function Designer** 定义文本、数字、开关字段及默认值。基础操作支持 `{{name}}` 模板、求和、乘积和按顺序拼接，可在测试区试运行。自定义按钮逻辑绑定仍是保留元数据，不会执行任意代码。
4. **Card Printer** 连接一个卡面设计器和一个功能设计器。保存设计，再点击 **Print Card**。打开打印出的卡片填写表单并运行；修改设计器不会自动改变已打印卡片。
5. **Packer** 连接 Pack Designer，接收从画布或 Library 拖来的卡片、Legion 引用。删除引用不会删除原始对象；导出使用对象当前配置。

通用参数按字段 ID 匹配并覆盖自定义卡片默认值，值必须符合字段类型。其他插件卡片保留自己的配置。

## 导出与安装

点击 **Check Pack** 查看依赖和问题，再用 **Export .oawpack** 重新检查并生成安装文件。**Share File** 使用系统分享功能；不可用时浏览器下载文件。

接收者在 **Library → Packs → Install Pack from File...** 安装，按提示重启，再开包、加入牌组和放置卡片。不需要原作者的工厂设备；仍须满足安装计划中的渲染器和其他卡包依赖。

默认不包含初始文档、资源与共享状态，需要时显式选择。升级保持 Pack ID 并提高版本。同一 ID/版本不可覆盖；升级不会改写已有画布实例。

## 提交到 Marketplace

在 Pack Designer 中设置你有权限的命名空间 ID（如 `your-namespace.my-cards`），保存并导出成功后，点击 **Publish on Marketplace** 打开作者网站。在浏览器中登录，手动选择导出文件并提交审核。按钮不会传送文件或凭据，提交不代表已经发布。离线分享可以保留 `local.*` ID。

## 完整代码开发

Pack Designer 的 **Developer tools → Pack IDE（Experimental）** 提供仍在开发的视觉源码编辑流程。传统工厂制作不因此变成实验性功能。需要自定义 Python/React 行为时使用[外部 SDK 开发指南](developers/index.md)。格式与安全限制见[配方参考](reference/factory-recipes.md)（英文）。
