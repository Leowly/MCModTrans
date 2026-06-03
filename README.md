# ModTrans — Minecraft Mod 汉化工具

自动提取 Minecraft 整合包中所有 Mod 的语言文件，调用 AI 批量翻译为简体中文，输出标准资源包。

支持 **1.12.2-**（`.lang` 格式）和 **1.13+**（`.json` 格式）。

## 快速开始

```powershell
# 1. 安装依赖
uv sync

# 2. 生成配置文件
uv run modtrans init-config

# 3. 编辑 modtrans.toml，填入 API 密钥
notepad modtrans.toml
```

## 命令总览

| 命令 | 用途 | 阶段 |
|------|------|------|
| `translate` | **完整翻译流水线** — 解析 → 分析 → AI 翻译 → 打包输出 | 🚀 核心 |
| `analyze` | **分析整合包** — 翻译覆盖率、i18n 匹配、未命名物品统计 | 🔍 分析 |
| `inspect` | **深度查看单个 Mod** — 元数据、条目详情、样本数据 | 🔬 调试 |
| `find-untagged` | **查找缺失英文名的物品/方块** — 基于模型文件扫描 | 🔍 分析 |
| `i18n` | **查看 i18n 自动汉化数据** — 各 MC 版本已有汉化统计 | 📖 参考 |
| `parse` | **导出所有语言数据为 JSON** — 调试用 | 🔬 调试 |
| `cache` | **管理解析缓存** — 查看/清除缓存 | 🔧 维护 |
| `init-config` | **生成示例配置文件** | 🚀 快速开始 |

---

## 命令详解

### `translate` — 完整翻译流水线

最核心的功能。一条命令完成从解析到打包的全流程。

```powershell
modtrans translate
modtrans translate -m "D:\path\to\modpack"
modtrans translate --dry-run              # 仅解析，不调用 AI
modtrans translate --generate-untagged    # 为无语言文件的 mod 自动生成英文名
```

**执行流程（6 个阶段）：**

```
mods/*.jar  ──Step 1──>  解析语言文件
                           ├─ 提取 en_us, zh_cn 条目
                           └─ 缓存解析结果（加速二次运行）
                          ↓
                   ──Step 2──>  补充 i18n 汉化（可开关）
                          ↓
                   ──Step 3──>  跨模组缺失键检测（可开关）
                                 识别 A 模组引用了 B 模组的效果/物品
                                 但 B 新增了条目而 A 未更新
                          ↓
                   ──Step 4──>  模型文件未命名物品补充（可开关）
                                 扫描 JAR 内的 models/item/*.json
                                 和 blockstates/*.json，
                                 自动生成未命名物品/方块对应的语言键
                          ↓
                   ──Step 5──>  AI 翻译
                                 按作者分批 → 翻译记忆库去重 →
                                 调用 AI 翻译 → 集中补译遗漏条目
                          ↓
                   ──Step 6──>  打包输出资源包
                                 pack.mcmeta + assets/<modid>/lang/zh_cn.*
```

**输出目录结构：**

```
modtrans_output/
├── pack.mcmeta
└── assets/<modid>/lang/
    ├── zh_cn.lang   (1.12.2-)
    └── zh_cn.json   (1.13+)
```

复制到 Minecraft 的 `resourcepacks/` 文件夹即可使用。

---

### `analyze` — 分析整合包

概览整个整合包的翻译现状，找出哪些 Mod 还没翻译、哪些有未命名物品。

```powershell
modtrans analyze
modtrans analyze -m "D:\path\to\modpack"
```

**输出内容：**

- **翻译覆盖率** — 每个 Mod 的英文条目数、已有中文条目数
- **i18n 匹配** — 哪些 Mod 有 i18 的自动汉化数据
- **未命名物品/方块** — JAR 内模型文件中缺少对应语言条目的项
  （详见「未命名物品检测机制」章节）
- **已 100% 翻译的 Mod** — 绿字列出

---

### `inspect` — 深度查看单个 Mod

针对单个 JAR 文件查看详细信息，适合调试或了解某个 Mod 具体有多少文本。

```powershell
modtrans inspect "D:\path\to\mod.jar"
```

**输出内容：**

```
JAR 详细信息
  Mod ID         modid
  名称/作者/版本 元数据
  MC 版本        语言文件格式 (.lang / .json)
  编码            UTF-8 / GBK / UTF-16

  英文条目数      已翻译数 / 中文残留英文数
  中文条目样本    前 20 条 en_us / zh_cn
  废弃键          zh_cn 有但 en_us 已删除的键
```

---

### `find-untagged` — 查找缺失英文名的物品/方块

专门用于检测「未命名物品」—— 即游戏中显示原始 ID
（如 `item.modid:redstone_sword.name`）而不是可读名称的物品。

```powershell
modtrans find-untagged
modtrans find-untagged -m "D:\path\to\modpack"
```

**输出分级：**

1. **完全无语言文件的 Mod** — JAR 里没有 en_us 文件，所有物品都无法显示正常名称
   - 显示自动建议的语言键和英文名
2. **有语言文件但仍有未命名物品的 Mod** — 部分物品模型缺少对应语言条目
   - 具体列出是哪些物品/方块缺少名称
3. **完全正常的 Mod** — 所有模型物品均有语言条目

**提示：** 对无语言文件的 Mod，可使用 `modtrans translate --generate-untagged`
自动生成英文名并翻译。

---

### `i18n` — 查看 i18n 自动汉化数据

浏览 CFPA 社区维护的各 MC 版本 i18n 汉化数据概况。

```powershell
modtrans i18n              # 显示所有可用版本摘要
modtrans i18n -v 1.12.2    # 查看 1.12.2 版本各个 Mod 的条目数
```

这些汉化数据会在 `translate` 运行时自动匹配使用，一般不需要手动操作。

---

### `parse` — 导出语言数据为 JSON

将所有 Mod 的语言条目导出为一个 JSON 文件，用于外部脚本处理或手动检查。

```powershell
modtrans parse -o analysis.json
modtrans parse -m "D:\path\to\modpack" -o analysis.json
```

---

### `cache` — 管理解析缓存

`translate` 首次运行时会缓存每个 JAR 的解析结果，二次运行极快。
此命令用于查看或清理缓存。

```powershell
modtrans cache --stats   # 显示缓存统计（条目数、大小、位置）
modtrans cache --clear   # 清空缓存
```

---

### `init-config` — 生成配置文件

```powershell
modtrans init-config              # 生成 modtrans.toml
modtrans init-config -o myconfig.toml  # 指定文件名
```

---

## 配置文件 `modtrans.toml`

```toml
[ai]
api_base = "https://api.openai.com/v1"   # 第三方 API 改这里
api_key = "sk-your-key-here"             # 直接填密钥
model = "gpt-4o"                          # 模型名
```

---

## 未命名物品检测机制

Minecraft 中，如果物品/方块没有对应的语言键（lang key），游戏会显示原始 ID，
如 `item.spartanshields:shield_basic_wood.name`。ModTrans 通过扫描 JAR
内部的实际模型文件来检测这些缺失。

**检测流程：**

1. **扫描模型文件** — 遍历 JAR 内 `models/item/*.json`（物品）和
   `blockstates/*.json`（方块），提取文件名作为待检测列表
2. **获取已知语言键** — 从 `en_us.lang` / `en_us.json` 提取所有已有条目
3. **交叉匹配** — 对每个模型文件名，尝试所有可能的键名格式：
   - 冒号格式: `item.<modid>:<name>.name`（Forge 1.12.2+ 标准）
   - 点格式:   `item.<modid>.<name>.name`（Legacy）
   - 无后缀:   `item.<modid>:<name>`（部分 Mod 的非标准用法）
   - 模糊匹配: 通过模型根路径匹配
4. **报告未命名** — 没有任何格式能匹配到语言键的模型，标记为"未命名"

**一致性保证：** `translate`、`analyze`、`find-untagged` 三个命令共享同一套
检测逻辑（`model_scanner.py`），确保统计结果完全一致。

---

## 工作流程

```
mods/*.jar → 解析语言文件 → 按作者分批 → AI 翻译 → 输出资源包
```

- 已有正确汉化自动跳过
- zh_cn 中仍是英文的提交 AI 判断（专有名词保留，漏翻的翻译）
- 系统提示词不变，最大化 API 缓存命中率

## 第三方 API 示例

**DeepSeek：**
```toml
[ai]
api_base = "https://api.deepseek.com/"
api_key = "sk-your-deepseek-key"
model = "deepseek-v4-flash"
```