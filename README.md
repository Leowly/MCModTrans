# ModTrans — Minecraft Mod 汉化工具

自动提取 Minecraft 整合包中所有 Mod 的语言文件，调用 AI 批量翻译为简体中文，输出标准资源包。

支持 **1.12.2 及以下**（`.lang` 格式）和 **1.13+**（`.json` 格式）。

## 快速开始

```bash
uv sync
modtrans init-config
```

编辑 `modtrans.toml`，填入你的 API 密钥：

```toml
[ai]
api_key = "sk-your-api-key-here"
```

然后运行：

```bash
modtrans analyze -m /path/to/mods
modtrans translate -m /path/to/mods -o ./chinese_pack
```

## 命令参考

| 命令 | 说明 |
|------|------|
| `modtrans translate` | 完整流水线：解析 → 翻译 → 打包 |
| `modtrans analyze` | 分析 Mod 翻译覆盖率 |
| `modtrans inspect <jar>` | 深入查看单个 JAR |
| `modtrans find-untagged` | 查找缺少英文名的物品 |
| `modtrans cache --clear` | 清除解析缓存 |

## 配置文件

只有这几个字段需要关心，其余都有合理默认值：

```toml
[general]
mods_dir = "./mods"          # 整合包 mods 目录
output_dir = "./output_resource_pack"
game_version = "auto"        # auto / legacy / modern

[ai]
api_base = "https://api.openai.com/v1"
api_key = "sk-your-key-here"  # 直接写密钥
model = "gpt-4o"
```

## 工作流程

```
mods/*.jar → 解析语言文件 → 按作者分批 → AI 翻译 → 输出资源包
```

- 已有正确汉化自动跳过
- zh_cn 中仍是英文的提交 AI 判断（专有名词保留，漏翻的翻译）
- 系统提示词不变，最大化 API 缓存命中率

## 输出

放入 `resourcepacks/` 即可使用：

```
output_resource_pack/
├── pack.mcmeta
└── assets/<modid>/lang/
    ├── zh_cn.lang   (1.12.2-)
    └── zh_cn.json   (1.13+)
```

## 第三方 API

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
