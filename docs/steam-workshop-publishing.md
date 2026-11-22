# 《深空当铺：可能是偷的》Steam 创意工坊发布指南

核对日期：2026 年 11 月 22 日。**游戏 Mod 用 SteamCMD 上传到 AppID `4348910`；独立的 Windows 模组管理器 EXE 不应作为该游戏的工坊 Mod 发布。** [开发者中文指南][game-guide] · [Valve 工坊实现文档][valve-workshop]

## 上传游戏 Mod

1. **准备发布目录。** 安装 SteamCMD，使用拥有正式版的 Steam 账号；试玩版和 Playtest 不支持工坊，游戏内没有上传按钮。`contentfolder` 指向单个 Mod 的目录（不是整个 `Mods/` 或 ZIP）。根目录放 `manifest.xml`，其中要有 `<Assemblies>` 和 `<Capabilities>`；至少有一个实现 `IMod` 的 DLL，并把所有随附 DLL 列入 `<Assemblies>`。建议附一张小于 1 MB 的 `preview.png`。按实测填写 `<GameVersions>`；有前置时写入 `<Prerequisites>` 并在工坊说明中列出，本作不会替玩家自动订阅前置。[发布流程][game-guide] · [Mod 目录要求][game-resources]
2. **创建 VDF。** 将示例另存为 `C:\Workshop\upload.vdf`，替换实际发布路径、标题和描述；VDF 内的路径用双反斜杠。`publishedfileid` 为 `0` 表示新建。首测用 `visibility` 值 `2`（仅作者可见）；`1` 仅好友，`0` 公开。[开发者中文指南][game-guide] · [Valve 可见性定义][valve-visibility]

   ```vdf
   "workshopitem"
   {
       "appid"           "4348910"
       "publishedfileid" "0"
       "contentfolder"   "C:\\Workshop\\Release\\PriceTweaks"
       "previewfile"     "C:\\Workshop\\Release\\PriceTweaks\\preview.png"
       "visibility"      "2"
       "title"           "Price Tweaks"
       "description"     "Adjusts item prices."
       "changenote"      "Initial release"
   }
   ```

3. **提交并保存条目 ID。** 在 SteamCMD 所在目录打开 PowerShell，替换 `YOUR_STEAM_LOGIN` 后运行。按提示完成登录和 Steam Guard（如有），不要把密码或验证码写进命令、VDF 或脚本。成功后 SteamCMD 会把条目 ID 写回 VDF 的 `publishedfileid`。首次上传还需用作者账号在 Steam 网站接受创意工坊法律协议，否则条目不会公开。[开发者中文指南][game-guide] · [Valve 工坊实现文档][valve-workshop]

   ```powershell
   .\steamcmd.exe +login YOUR_STEAM_LOGIN +workshop_build_item "C:\Workshop\upload.vdf" +quit
   ```

4. **实测后公开，日后更新原条目。** 在[本作工坊][game-workshop]以作者身份订阅该条目。先移走 `Mods/` 中同 `<ID>` 的本地副本（否则它会覆盖工坊版），等 Steam 下载完，在正式版「模组」菜单中审核并启用，随后重启验证。通过后在条目页改为公开，并把 VDF 的 `visibility` 同步为 `0`。以后更新内容和 `changenote`，保留回填的 `publishedfileid` 再跑同一命令；不要改回 `0`。订阅者会收到更新。[开发者中文指南][game-guide] · [Valve 工坊实现文档][valve-workshop]

## 独立管理器 EXE 怎么发

- **不要把 EXE 伪装成 Mod 或把它放进 Mod 的发布目录。** Valve 的工坊上传接口接收文件夹，但订阅下载不会注册一个可启动的 Steam 应用。本作期待原生 Mod 目录；官方指南明确说工坊目录中的可执行文件会被扫描，不应附带安装程序，且拒绝 Mod 启动其他程序或内嵌可执行资源。这不代表 Steam 上传端必然拒收所有 `.exe`，而是本作没有受支持的 EXE 工坊分发流程。[Valve 工坊实现文档][valve-workshop] · [开发者扫描规则][game-scan] · [拒绝项][game-rejections]
- **建议走项目自己的发布页**（如 GitHub Releases），附带版本号、SHA-256 校验值和使用说明。若要在 **Steam 商店**分发，走 Steam Direct，为工具创建独立的 `Software` 应用及 AppID。Valve 将 `Player Tools` 列为可能接受的非游戏软件类别，仍需商店页和构建审核，不保证获批。[Steamworks 入驻指南][steam-direct] · [应用类型][steam-apps] · [发行流程][steam-release]
- **Valve 的 `App Publish Permissions` 是另一回事。** 游戏方可授权某个独立工具的 AppID 向本作工坊**提交 Mod 内容**；这不是发布工具 EXE 本身的权限。[Valve 工坊实现文档末尾 FAQ][valve-workshop]

仅核对公开资料；未登录账号、上传内容或实际测试订阅。

[game-guide]: https://github.com/dragonpan2/probably-stolen-modding/blob/main/ModdingGuide.zh-CN.md#steam-创意工坊
[game-resources]: https://github.com/dragonpan2/probably-stolen-modding/blob/main/ModdingGuide.zh-CN.md#模组资源
[game-scan]: https://github.com/dragonpan2/probably-stolen-modding/blob/main/ModdingGuide.zh-CN.md#能力声明与扫描器
[game-rejections]: https://github.com/dragonpan2/probably-stolen-modding/blob/main/ModdingGuide.zh-CN.md#一律拒绝的情况
[game-workshop]: https://steamcommunity.com/app/4348910/workshop/
[valve-workshop]: https://partner.steamgames.com/doc/features/workshop/implementation
[valve-visibility]: https://partner.steamgames.com/doc/api/ISteamRemoteStorage#ERemoteStoragePublishedFileVisibility
[steam-direct]: https://partner.steamgames.com/doc/gettingstarted/onboarding
[steam-apps]: https://partner.steamgames.com/doc/store/application
[steam-release]: https://partner.steamgames.com/doc/store/releasing
