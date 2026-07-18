---
name: 3d66 标签系统
description: 建筑设计刊物与精密图像评测台结合的明亮产品界面
colors:
  primary: "#CCED46"
  ink: "#11130F"
  canvas: "#FFFFFF"
  workspace: "#F3F5F0"
  line: "#E7EAE3"
  line-strong: "#D9DED3"
  muted: "#62685F"
typography:
  display:
    fontFamily: "Microsoft YaHei, 微软雅黑, PingFang SC, sans-serif"
    fontSize: "3rem"
    fontWeight: 700
    lineHeight: 1.08
    letterSpacing: "-0.025em"
  body:
    fontFamily: "Microsoft YaHei, 微软雅黑, PingFang SC, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
rounded:
  sm: "4px"
  md: "6px"
  lg: "8px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "10px 16px"
  input:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "10px 12px"
---

# Design System: 3d66 标签系统

## 1. Overview

**Creative North Star: “建筑设计刊物 × 精密评测台”**

界面采用明亮、克制的编辑排版，全部文字统一使用微软雅黑，通过字号、字重和间距建立层级。结构依赖非对称网格、淡冷灰分割线、明确的编号与测量感细节，而不是依赖卡片、阴影或装饰。

图片始终在白色评测环境中展示。酸性青柠是稀缺的状态信号，只在主操作、当前选择、进度和焦点出现。

**Key Characteristics:**

- 白底审图与高对比文字
- 全局微软雅黑字体，通过字重区分标题与操作控件
- 极浅结构线与少量中等强度外边界
- 非对称但可预测的高密度产品布局
- 酸性青柠作为唯一强调色

## 2. Colors

冷白和中性绿灰构成长期审核环境，酸性青柠提供清晰而稀缺的品牌信号。

### Primary

- **3D66 Acid Lime**：只用于主操作、选中状态、进度和焦点，任何单屏面积不超过 15%。

### Neutral

- **Editorial Ink**：标题、正文、图标和数据的主要墨色。
- **Inspection Canvas**：图片评测画布和内容面板。
- **Cool Workspace**：页面背景与次级工具区域。
- **Whisper Line**：内部网格、表格规则和普通分隔。
- **Structural Line**：外边界和关键结构分隔。

### Named Rules

**The One Signal Rule.** 酸性青柠不承担装饰，只承担行动、选择、进度和焦点。

**The White Inspection Rule.** 图片评测区域永远为白色或极浅中性底，不提供暗色模式。

## 3. Typography

**Display Font:** Microsoft YaHei（微软雅黑）

**Body Font:** Microsoft YaHei（微软雅黑）

**Label/Data Font:** Microsoft YaHei（微软雅黑）

**Character:** 全站以微软雅黑保持统一和清晰，通过粗细、字号及留白延续编辑式层级；数字启用等宽数字特性以方便纵向比较。

### Hierarchy

- **Display**（700，3rem，1.08）：品牌标题和少量关键页面标题。
- **Headline**（700，1.75rem，1.2）：页面标题。
- **Title**（600，1.125rem，1.35）：区块标题和证据栏标题。
- **Body**（400，1rem，1.5）：正文与表单内容，说明文字不超过 70ch。
- **Label**（500，0.8125rem，0.02em）：工具标签、状态和元数据。

### Named Rules

**The Unified Typeface Rule.** 标题、正文、按钮、输入框、表格、数字与小字号标签全部使用微软雅黑，不再混用衬线或等宽字体。

## 4. Elevation

系统默认扁平，通过背景层级、1px 边界和留白表达深度。阴影只用于浮层和拖拽对象，不用于普通面板。

### Shadow Vocabulary

- **Floating Panel** (`0 16px 48px rgba(17, 19, 15, 0.10)`): 仅用于菜单、Popover 和对话框。

### Named Rules

**The Flat-by-Default Rule.** 静态表面不使用阴影；如果一个面板必须靠阴影才能被理解，先修正结构和间距。

## 5. Components

### Buttons

- **Shape:** 紧凑直角感（4px）。
- **Primary:** 酸性青柠底、墨色文字，最小高度 44px。
- **Hover / Focus:** hover 轻微降低明度；focus 使用清晰墨色外环。
- **Secondary / Ghost:** 白底细边或无底色，不引入第二强调色。

### Chips

- **Style:** 小圆角、细边界、短文本；仅状态标签允许轻微底色。
- **State:** 选中状态使用青柠标记或底色，不采用满屏胶囊。

### Cards / Containers

- **Corner Style:** 6px。
- **Background:** 白色或冷中性工作区。
- **Shadow Strategy:** 默认无阴影。
- **Border:** 内部分隔使用极浅线，外边界按需使用结构线。
- **Internal Padding:** 16px 或 24px。

### Inputs / Fields

- **Style:** 白底、4px 圆角、1px 结构线。
- **Focus:** 青柠外环配墨色边界。
- **Error / Disabled:** 错误同时包含图标与文字；禁用状态保持可读。

### Navigation

窄酸性青柠索引轨与白色功能导航组合。活动项必须同时具备位置、文字和图形状态，移动端折叠为顶部导航。

### Evidence Rail

证据栏以淡分割线组织八个审美维度。等级、置信度、证据和缺陷紧邻展示，不使用嵌套卡片。

## 6. Do's and Don'ts

### Do:

- **Do** 在白底上展示原图，并保留明确但轻量的图像边界。
- **Do** 使用微软雅黑的字号和字重变化建立页面标题层级。
- **Do** 使用极浅冷灰线组织高密度信息。
- **Do** 为所有模型结论显示证据、置信度和版本。
- **Do** 为键盘焦点、加载、错误、空状态和冲突提供完整反馈。

### Don't:

- **Don't** 使用暗色审图画布或暗色模式。
- **Don't** 使用传统模板化后台、玻璃拟态、紫蓝渐变或霓虹光晕。
- **Don't** 使用大面积酸性青柠、青柠渐变或低对比青柠文字。
- **Don't** 堆叠胶囊、重阴影、重复卡片网格或深色分割线。
- **Don't** 在任何页面区域混用衬线、等宽或其他装饰字体。
