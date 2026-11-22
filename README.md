# 深空当铺：可能是偷的 · 模组管理器

《深空当铺：可能是偷的》（Probably Stolen）的 Windows 桌面模组管理器。

## 下载 Windows 版

**[直接下载 v0.2.1 Windows x64 EXE](https://github.com/SIYYIS/ProbablyStolenModManager/releases/download/v0.2.1/ProbablyStolenModManager-Desktop-v0.2.1-x64.exe)** · [发布页](https://github.com/SIYYIS/ProbablyStolenModManager/releases/latest)

下载后双击 EXE 即可打开独立窗口，不用解压或安装 Python、Node.js。系统需有 Microsoft Edge WebView2 Runtime。

## 功能

- 扫描、排序与管理本地模组和 Steam 工坊模组，检查前置依赖与资源冲突。
- 创建和切换模组预设，编辑模组配置与游戏存档，管理备份。
- 搜索创意工坊、查看模组更新时间，分析游戏报错并导出排错包。

## 软件数据位置

默认数据目录是 EXE 同级的 `manager_data/`；从源码运行时，是项目根目录下的 `manager_data/`。可通过 `--data-dir` 指定其他位置。该目录仅供本机使用，不会提交到本仓库。部分文件夹会在首次使用相应功能时创建。

| 路径 | 用途 |
| --- | --- |
| `profiles/` | 保存的模组预设及其隔离的 `ModConfigs/` 配置副本；不包含模组本体。 |
| `backups/` | 切换预设前的存档备份，以及该次切换涉及的 `ModConfigs/` 备份。 |
| `snapshot_history/` | 存档快照时间线、预设与存档槽位的关联记录，以及导出的快照。 |
| `slot_backups/` | 编辑或恢复 `save_<槽位号>.es3` 前自动保存的原始档位。 |
| `config_backups/` | 修改模组配置文件前自动保存的原文件。 |
| `workshop_covers/` | 本地缓存的创意工坊模组封面图片，避免反复下载。 |
| `downloads/` | 安装模组压缩包时使用的下载与解包暂存文件。 |
| `settings.json` | 游戏、工坊和存档目录等设置；如果填写了 Steam Web API Key，也会保存在这里。 |
| `diagnostic-ai.json` | AI 报错分析的 API 地址、模型和可选 API Key。此文件保存在本机，但 **API Key 未加密**，请勿分享或上传。 |
| `mod-workbench.json` | 本地模组列表的分隔条、颜色标签和个人备注。 |
| `third-party-sources.json` | 你手动添加的 GitHub 或 Nexus Mods 来源链接。 |
| `workshop_update_times.json` | 已查询到的 Steam 创意工坊公开更新时间缓存。 |
| `profile-activity-<标识>.json` | 预设启动次数及最近使用信息；文件名标识由存档路径计算，不直接保存该路径。 |

**游戏自己的数据不在 `manager_data/` 中。** 存档、档位索引、`ModConfigs/` 和 `Player.log` 通常位于 `%USERPROFILE%\AppData\LocalLow\Questing Goose Studio\Probably Stolen\`；本地模组位于游戏安装目录的 `Mods/`，Steam 工坊模组位于 Steam 库的 `steamapps/workshop/content/4348910/`。实际位置以「路径与设置」中显示的路径为准。管理器不会把这些本地数据自动上传到 GitHub；AI 报错分析只有在你确认发送后，才会将预览中的日志片段发往所配置的 API。

## 从源码运行

```powershell
py -3.10 -m pip install -r requirements-desktop.txt
npm --prefix frontend ci
npm --prefix frontend run build
py -3.10 main.py
```

运行 `build_desktop.bat` 可重新打包 EXE。
