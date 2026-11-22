# 深空当铺：可能是偷的 · 模组管理器

《深空当铺：可能是偷的》（Probably Stolen）的 Windows 桌面模组管理器。

## 下载 Windows 版

**[直接下载 v0.2.1 EXE](https://github.com/SIYYIS/ProbablyStolenModManager/releases/download/v0.2.1/ProbablyStolenModManager-Desktop-v0.2.1.exe)** · [发布页](https://github.com/SIYYIS/ProbablyStolenModManager/releases/latest)

下载后双击 EXE 即可打开独立窗口，不用解压或安装 Python、Node.js。系统需有 Microsoft Edge WebView2 Runtime。仓库为私有，下载时请登录有访问权限的 GitHub 账号。

## 功能

- 扫描、排序与管理本地模组和 Steam 工坊模组，检查前置依赖与资源冲突。
- 创建和切换模组预设，编辑模组配置与游戏存档，管理备份。
- 搜索创意工坊、查看模组更新时间，分析游戏报错并导出排错包。

## 从源码运行

```powershell
py -3.10 -m pip install -r requirements-desktop.txt
npm --prefix frontend ci
npm --prefix frontend run build
py -3.10 main.py
```

运行 `build_desktop.bat` 可重新打包 EXE。
