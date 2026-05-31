# ModTrans — Minecraft Mod 汉化工具

自动提取 Minecraft 整合包中所有 Mod 的语言文件，调用 AI 批量翻译为简体中文，输出标准资源包。

支持 **1.12.2 及以下**（`.lang` 格式）和 **1.13+**（`.json` 格式）。

## 快速开始

```powershell
# 1. 安装依赖
uv sync

# 2. 生成配置文件
uv run modtrans init-config

# 3. 编辑 modtrans.toml，填入 API 密钥
notepad modtrans.toml
```

## 使用

```powershell
# 运行命令前，先激活虚拟环境（每次新终端只需一次）
.venv\Scripts\activate

# 分析整合包的翻译覆盖率（会弹出文件夹选择器）
modtrans analyze

# 也可以用 -m 直接指定路径（支持整合包根目录或 mods 文件夹，自动识别）
modtrans analyze -m "D:\path\to\modpack"

# 深入了解某个 Mod
modtrans inspect "D:\path\to\mod.jar"

# 完整翻译
modtrans translate

# 先试运行看效果（不调用 AI，仅解析）
modtrans translate --dry-run
```

如果不想每次激活虚拟环境，用 `uv run` 前缀：

```powershell
uv run modtrans analyze
uv run modtrans translate
```

## 配置文件 `modtrans.toml`

```toml
[ai]
api_base = "https://api.openai.com/v1"   # 第三方 API 改这里
api_key = "sk-your-key-here"             # 直接填密钥
model = "gpt-4o"                          # 模型名
```

`modtrans.toml` 已加入 `.gitignore`，不会被提交到 Git。

## 工作流程

```
mods/*.jar → 解析语言文件 → 按作者分批 → AI 翻译 → 输出资源包
```

- 已有正确汉化自动跳过
- zh_cn 中仍是英文的提交 AI 判断（专有名词保留，漏翻的翻译）
- 系统提示词不变，最大化 API 缓存命中率

## 输出

```
modtrans_output/
├── pack.mcmeta
└── assets/<modid>/lang/
    ├── zh_cn.lang   (1.12.2-)
    └── zh_cn.json   (1.13+)
```

将 `modtrans_output/` 复制到 Minecraft 的 `resourcepacks/` 文件夹即可使用。

## 第三方 API 示例

**DeepSeek：**
```toml
[ai]
api_base = "https://api.deepseek.com/v1"
api_key = "sk-your-deepseek-key"
model = "deepseek-chat"
```

**通义千问：**
```toml
[ai]
api_base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
api_key = "sk-your-qwen-key"
model = "qwen-plus"
```
