# Image2Visio

当前版本：`v1.0.0`

**Image2Visio** 是一个 Codex Skill，用于把参考图片、截图或已有 `.vsdx` 还原为 **Microsoft Visio 原生可编辑图形**，并从同一份 Visio 源文件导出 PNG、SVG、PDF、PPTX 等交付格式。

它综合了两条成熟工作流的优势：

- 来自 **figedit**（FigEdit · 图易编）的语义 Manifest 管线：OCR/CV 测量证据、元素路由决策、公式矢量重建、最小必要栅格裁剪与质量审计。
- 来自 **visio-image-rebuilder**（Visio Image Rebuilder）的 Visio 原生重建能力：COM 自动化绘图、面板标定与防重叠、样式迁移、多格式导出与 `.vsdx` 包结构检查。

感谢 **figedit** 与 **visio-image-rebuilder** 两个 skill 的设计与实现，Image2Visio 在保留 Visio 可编辑性的同时，也能系统化处理复杂多面板科学图、混合截图/图表合成图和公式密集型配图。

> **适用边界**：主要面向模型图、架构图、流程图、方法示意图等结构化配图。含真实数据结果的指标图、统计图等，仍建议优先用脚本或先提取数据再绘图。

## 适用场景

适合：

- 根据 PNG/JPG/截图重建 Visio 图。
- 将 AI 生成的论文模型图转成可编辑 `.vsdx`。
- 按参考图修改已有 Visio 文件的布局、配色、字体或模块结构。
- 对复杂多面板科学图进行结构化复刻。
- 检查 `.vsdx` 是否误用了整张参考图嵌入。
- 给 Visio 图统一论文风格字体、配色和线条规范。
- 从保存后的 `.vsdx` 导出 SVG、PDF、PPTX 或 PNG。
- 对复杂多面板图先做面板四角/边界标定，减少子模块移位、串区和重叠。

不适合：

- 只需要把图片插入 Visio 页面。
- 只需要普通图片编辑、抠图或美化。
- 不要求 Visio 原生可编辑性的纯位图复刻。

## 核心原则

最终交付的 `.vsdx` 应尽量由以下对象构成：

- Visio 原生矩形、圆形、线条、箭头、连接线。
- 可编辑文本。
- 可编辑分组。
- 公式矢量组（带 LaTeX 元数据）。
- 原生近似绘制的小图表、热图、节点图、立方体、堆叠图。
- 仅对 Logo、截图、地图、照片、密集图表体等源特定视觉对象保留最小裁剪。

禁止用整张参考图作为最终页面内容来冒充还原。参考图只能作为临时描摹依据；最终文件中不应留下完整的大尺寸参考 PNG/JPG。

`.vsdx` 是可编辑母版。SVG/PDF/PPTX 是从这个母版导出的交付物，不应该单独重画出彼此不一致的版本。

对于复杂或混合图形，OCR 与 OpenCV 输出只是测量证据，不是自动绘图计划。Agent 应编写语义 `manifest.json`，由 compose 管线按元素类型路由到 Visio 原生对象、公式组或最小栅格资产。

## 仓库结构

```text
.
├── README.md
├── SKILL.md
├── agents/
│   └── openai.yaml
├── examples/
│   └── manifest.example.json
├── references/
│   ├── manifest-pipeline.md
│   └── rebuild-guidelines.md
├── scripts/
│   ├── audit_visio_package.py
│   ├── compose_visio_package.ps1
│   ├── figedit_core.py
│   ├── prepare_measurements.py
│   ├── prepare_visio_manifest.py
│   ├── validate_manifest.py
│   ├── visio_export_formats.ps1
│   ├── visio_manifest_renderer.ps1
│   ├── visio_page_tools.ps1
│   └── visio_rebuild_scaffold.ps1
├── templates/
│   └── manifest.schema.json
└── tests/
    └── test_core.py
```

文件说明：

- `SKILL.md`：Codex Skill 主入口，包含触发描述、双工作流、验收标准与安全规则。
- `agents/openai.yaml`：Codex UI 元数据。
- `references/manifest-pipeline.md`：语义 Manifest 管线契约、元素路由表与坐标约定。
- `references/rebuild-guidelines.md`：视觉匹配、面板拆解、样式参数、导出策略与验证 rubric。
- `scripts/prepare_measurements.py`：生成 OCR、OpenCV、样式 token 与 draft manifest 等测量证据。
- `scripts/validate_manifest.py`：校验 manifest 结构与路由约束。
- `scripts/prepare_visio_manifest.py`：裁剪资产、渲染公式 SVG、准备 Visio 渲染输入。
- `scripts/compose_visio_package.ps1`：默认 compose 入口，串联验证、渲染、预览、审计与多格式导出。
- `scripts/visio_manifest_renderer.ps1`：按 manifest 绘制 Visio 原生对象并写入 Shape Data。
- `scripts/audit_visio_package.py`：检查可编辑性与包结构。
- `scripts/visio_export_formats.ps1`：可复用导出函数，支持 PNG、SVG、PDF、PPTX。
- `scripts/visio_page_tools.ps1`：备份、导出、检查 `.vsdx` 包结构。
- `scripts/visio_rebuild_scaffold.ps1`：低层 Visio COM 绘图脚手架，适用于简单图或刻意手写脚本。
- `templates/manifest.schema.json`：manifest JSON Schema。
- `examples/manifest.example.json`：manifest 示例。

## 环境要求

推荐环境：

- Windows
- Microsoft Visio
- PowerShell
- Python 3（用于测量、manifest 校验与审计）
- Microsoft PowerPoint（用于 PPTX 导出）
- Git
- Codex Desktop 或支持本地文件与工具调用的 Codex 环境

说明：

- 完整 Visio 自动绘图依赖 Visio COM Automation，因此主要面向 Windows + Microsoft Visio。
- 语义 Manifest 管线额外依赖 Python 与 OCR/CV 相关包（见 `prepare_measurements.py` 运行提示）。
- SVG 和 PNG 由 Visio 页面导出；PDF 由 Visio 固定格式导出；PPTX 默认由 PowerPoint COM 插入 Visio 导出的 SVG 页面渲染。
- 不安装 Visio 时，仍可做 `.vsdx` 包结构检查、manifest 校验或有限 XML 修改，但不适合完整一比一重建。

## 安装方式

将本仓库克隆或复制到 Codex skills 目录。

Windows 示例：

```powershell
# 将本仓库复制或克隆到 Codex skills 目录，例如：
Copy-Item -Recurse "D:\path\to\image2visio" "$env:USERPROFILE\.codex\skills\image2visio"
```

安装后重启 Codex 或开启新会话，使 skill 被重新发现。

## 推荐使用方式

### 复杂图：语义 Manifest 管线（默认）

复杂、多面板、公式密集或图像混合的参考图，优先走 Manifest 管线。所有命令应在用户项目目录执行，产物写入任务目录（如 `figure-task/`），不要写回 skill 目录。

**1. 准备测量证据**

```powershell
python scripts\prepare_measurements.py input.png --out figure-task\work --ocr-profile v6_medium
```

**2. 检查诊断结果，编写语义 `manifest.json`**

将 OCR、OpenCV、采样颜色仅作证据；按元素类型路由为 redraw / text / math / image 等。详见 `references/manifest-pipeline.md`。

**3. 校验并 compose Visio 包**

```powershell
python scripts\validate_manifest.py figure-task\manifest.json

powershell -ExecutionPolicy Bypass -File scripts\compose_visio_package.ps1 `
  -ManifestPath figure-task\manifest.json `
  -VsdxPath figure-task\out\editable.vsdx `
  -OutputDir figure-task\out `
  -PageMode replace `
  -ExportFormats png,svg,pdf,pptx
```

compose 完成后检查 `preview.png`、`quality_report.md`、`editability_report.md` 与最终 `.vsdx`。

### 简单图：Visio 脚手架（legacy）

结构简单、模块较少、无需 manifest 路由的图，可直接基于 `visio_rebuild_scaffold.ps1` 手写 `Draw-ReferenceFigure` 脚本。将脚手架复制到工作区再改，不要直接修改 skill 目录内的副本。

```powershell
powershell -ExecutionPolicy Bypass -File scripts\visio_rebuild_scaffold.ps1 `
  -VsdxPath "C:\path\model.vsdx" `
  -PageW 16 `
  -PageH 9 `
  -RefW 1600 `
  -RefH 900 `
  -PreviewPath "C:\path\exports\model.png" `
  -ExportFormats svg,pdf,pptx `
  -OutputDir "C:\path\exports"
```

### 仅导出已有 `.vsdx`

```powershell
powershell -ExecutionPolicy Bypass -File scripts\visio_page_tools.ps1 `
  -VsdxPath "C:\path\model.vsdx" `
  -ExportFormats svg,pdf,pptx `
  -OutputDir "C:\path\exports" `
  -InspectPackage
```

### 示例请求

```text
使用 image2visio，根据这张参考图片重建 C:\path\model.vsdx，要求最终是 Visio 原生可编辑形状，不要整图嵌入，并导出 SVG、PDF、PPTX。
```

```text
对这张多面板方法图跑 prepare_measurements，写 manifest，用 compose_visio_package 重建并做质量审计。
```

```text
检查这个 .vsdx 是否只是嵌入了整张 PNG，如果是，请改成原生 Visio 形状重建，再导出 SVG。
```

## 面板标定与防重叠

复杂多面板图推荐流程：

1. 先标定整张参考图尺寸和 Visio 页面尺寸。
2. 再标定每个主要 panel 的左上角、宽高，必要时记录四角点。
3. panel 内部元素使用 0–1 局部坐标（manifest 中 `coordinate_space: "panel"`），而不是直接手写全图坐标。
4. 导出预览后检查子模块是否越出父 panel、相邻 panel 是否重叠、箭头和文字是否穿过无关模块。

`visio_rebuild_scaffold.ps1` 中提供 `RectRel`、`TextRel`、`OvalRel`、`LineRel`、`Assert-RelBox`、`Assert-RelPoint` 等 helper；Manifest 管线则在 compose 阶段对 panel-local 坐标做 containment 校验。

## 验收标准

一个合格的 Visio 还原结果应满足：

- 主体布局和参考图一致。
- 主要模块、标题、编号、箭头和说明文字齐全。
- 文字可编辑；图形对象可单独选中和修改。
- 没有整张参考图作为最终底图。
- 公式以可编辑矢量组呈现；源特定视觉仅保留必要裁剪。
- 配色、字体和线条风格统一。
- 复杂多面板图的内部元素不应明显移位、跨 panel 串区或互相重叠。
- 有原文件备份。
- 请求的 PNG/SVG/PDF/PPTX 从同一个保存后的 `.vsdx` 导出，并且文件非空。
- 已检查 preview、quality/editability 报告或 `.vsdx` 包结构（无 full-page raster 残留）。

## 致谢与来源

| 贡献来源 | 主要能力 |
|---|---|
| **figedit** | 语义 Manifest、OCR/CV 测量证据、元素决策门、公式重建、资产裁剪策略、质量审计思路 |
| **visio-image-rebuilder** | Visio COM 原生绘图、面板标定、局部坐标 helper、样式迁移、多格式导出、`.vsdx` 包检查 |

Image2Visio 不是简单拼接文档，而是把 figedit 的「证据 → manifest → 分元素路由 → 审计」流程，落到 Visio 作为最终可编辑母版的交付形态上。

## 版本历史

### v1.0.0 — 初始发布（Image2Visio）

- 以 **image2visio** 作为独立 skill 发布，统一 Manifest 管线与 Visio 原生重建双路径。
- 引入 `prepare_measurements.py`、`validate_manifest.py`、`prepare_visio_manifest.py`、`compose_visio_package.ps1`、`visio_manifest_renderer.ps1`、`audit_visio_package.py` 等 compose 工具链。
- 保留 `visio_rebuild_scaffold.ps1`、`visio_page_tools.ps1`、`visio_export_formats.ps1` 用于简单重建与导出。
- 提供 `manifest-pipeline.md`、`manifest.schema.json` 与示例 manifest。
- 明确致谢 figedit 与 visio-image-rebuilder。
