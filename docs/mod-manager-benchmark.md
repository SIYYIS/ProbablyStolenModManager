# 《深空当铺》模组管理器对标与开发优先级

核对日期：2026 年 11 月 21 日。范围：Probably Stolen Mod Manager（PSMM，Steam AppID `4348910`）源码中的桌面／浏览器工作台，以及四款代表性工具的一手资料。对标的是用户能完成的任务，不是功能数量或版本排名。

## 最值得做什么

**先把现有的「预览 → 诊断」接到「补齐 → 重验 → 确认应用」，再做能够核对实际模组内容的可移植方案。** 本项目不是缺分享码、自动排序、冲突提示或版本锁定；缺的是发现问题后不用来回找入口，以及换台机器后能判断两边是否运行同一套内容。保留游戏内授权、配置备份和正式档位保护，不为追求功能对齐引入虚拟文件系统或文件覆盖器。

## 四个代表：能借鉴什么

| 代表 | 一手资料确认的能力 | 对本项目有用的做法与边界 |
| --- | --- | --- |
| Mod Organizer 2（MO2） | 源码中 USVFS 映射目录和文件；预设维护 `modlist.txt`，可选预设本地存档与设置；冲突页分列覆盖与被覆盖的文件。[虚拟映射][mo2-vfs] · [预设][mo2-profile] · [冲突页][mo2-conflict] | 借鉴「来源、参与方、有效副本」的呈现。PSMM 的同 ID 来源选择与跨模组的静态资源重名不是一回事；不要把后者标成运行时谁覆盖谁。 |
| Vortex | Nexus Mods README 说明独立模组预设；源码按部署目标路径收集冲突，用 `before` / `after` / `conflicts` 规则表达选择，并有硬链接部署实现。[README][vortex-readme] · [冲突计算][vortex-conflict] · [规则编辑][vortex-rules] · [硬链接][vortex-deploy] | 借鉴把冲突说明和下一步放在同一界面。Vortex 的部署文件规则不等于本游戏存档里的加载顺序；无需照搬部署机制。 |
| RimSort | 官方指南说明模组列表导入／导出、依赖及不兼容告警、拓扑排序，以及分别显示模组声明、社区与用户的顺序规则；外部数据库是可选补充。[基本使用][rim-basic] · [排序][rim-sort] · [规则编辑][rim-rules] · [数据库][rim-db] | 最接近 PSMM 的排序场景。借鉴解释「为什么排在前面」和「规则从哪里来」，先解决已知前置；等本游戏有足够案例再考虑自定义或社区规则。 |
| r2modman | 项目 README 确认可切换预设、分享模组和配置、在管理器内下载／安装；导入界面支持文件或代码，并进入下载及填充预设步骤；依赖实现区分指定版本与其他安装策略。[README][r2-readme] · [导入流程][r2-import] · [依赖解析][r2-deps] | 借鉴让接收者看清哪些条目能补、哪些版本能复原。Thunderstore 的版本化包与 Steam 工坊条目不是同一种供给，不能承诺按工坊 ID 取到历史内容。 |

这些是流程借鉴，不是格式兼容声明。没有验证、也不建议承诺 PSMM 分享码与任一工具的预设、列表、压缩包或代码互换。

## 本项目有什么、真缺什么

状态按 [README](../README.md#核心功能) 与 `core`、`webui`、`frontend` 的调用路径交叉抽查。**已实现**指能找到实际读写或执行路径，不代表实机验收；**部分**指已有入口，但没有完成表中的后半步；**建议**指未在所查路径发现，不计入现有能力。

| 能力 | 状态与本地证据 | 容易误判的边界 |
| --- | --- | --- |
| 本地／工坊清单、启停、拖拽和排序 | **已实现。** [`scan_installed_mods`、`topological_sort_mods`](../core/scanner.py)、[`/order/preview`、`/order/diagnose`、`/order`](../webui/server.py)、[本地页](../frontend/src/LocalPage.tsx)和[应用入口](../frontend/src/App.tsx)。扫描时同 ID 本地副本优先；排序仅在当前启用集内按声明前置构边，循环时告警并保留未排项的原顺序。 | 自动排序不等于自动启用或安装前置，也不等于任意用户／社区规则引擎。 |
| 前置处理、版本和资源诊断 | **部分。** [`validate_active_mods`、`detect_asset_conflicts`](../core/scanner.py)区分未安装、未启用、顺序错误及版本声明问题，也列出掉落表目标和部分资源重叠；[本地页](../frontend/src/LocalPage.tsx)可跳到已安装前置，[诊断列表](../frontend/src/IssueList.tsx)折叠相同资源。 | 真缺的是递归前置修复方案、可靠来源匹配和修后重验。静态重叠不是运行时冲突结论；声明匹配不是兼容性保证。工作台在保存前显示差异和诊断，但现有[顺序写入](../webui/operations.py)及[预设应用](../webui/profile_launch.py)路径不以这些诊断作为统一写入门禁。 |
| 预设、配置与启动 | **已实现。** [`ProfileManager.save_profile` / `apply_profile`](../core/es3_manager.py)把普通 `SaveFile.es3` 预设限定在模组相关键，按预设切换 `ModConfigs`、写前备份；整档恢复另需确认。[对比路由](../webui/management_routes.py)与[对比页](../frontend/src/ProfileCompare.tsx)能显示顺序及可比较的配置差异。[启动入口](../webui/profile_launch.py)支持切换后启动、给普通预设创建打包 Windows 桌面快捷方式。[配置页](../frontend/src/ConfigPage.tsx)另有参数编辑。 | 预设能保存／恢复已有本机授权记录，不等于给未授权模组签名。关联档位在切换前备份，不自动切换游戏读哪个档。 |
| 分享与重建 | **部分。** [`export_share_code`](../core/es3_manager.py)定义 `PSMM1`，编码方案名、备注字段、有序模组 ID 与可选工坊 ID；[`/share/preview`](../webui/management_routes.py)找缺项，[分享弹窗](../frontend/src/App.tsx)可打开对应工坊页并把清单导入未保存的顺序。 | 仅是传清单：不包含模组文件、配置、存档、授权数据或足以核对内容一致的指纹。没有完整的补齐、验证与预设重建流程。 |
| 工坊获取、更新检查与锁定 | **已实现（本机）；跨机复原部分。** [工坊页](../frontend/src/WorkshopPage.tsx)和[路由](../webui/server.py)接通搜索、ID 查询与工坊入口，也可经[下载操作](../webui/operations.py)调用本机下载器单项获取。[工坊版本页](../frontend/src/WorkshopVersions.tsx)对应[`workshop_versions`、`change_version_lock`](../core/workshop_versions.py)：对照本机 ACF 与 Steam 公开时间，复制当前已安装工坊内容到本地 `Mods`，可解除或恢复该副本，拒绝覆盖已有同 ID 本地模组。 | 锁的是借同 ID 本地优先规则生效的本机副本，不是阻止 Steam 更新原件，也不按预设保存一套可下载的历史工坊版本。时间戳和作者版本文本不是内容一致的证明。 |
| 回退、排错与存档能力 | **已实现（所查路径）。** [备份时间线](../frontend/src/SnapshotTools.tsx)到[快照路由](../webui/snapshot_routes.py)及[历史核心](../core/snapshot_history.py)支持关联档位、导出副本、明确确认后恢复受支持的档位；[档位路由](../webui/slot_routes.py)与[存档写入](../core/save_slots.py)另提供有限字段编辑和备份。[诊断页](../frontend/src/DiagnosticsTools.tsx)、[诊断路由](../webui/diagnostic_routes.py)与[日志核心](../core/diagnostics.py)支持日志分析和可预览的脱敏包；[排错页](../frontend/src/TestSession.tsx)经[测试路由](../webui/test_routes.py)调用[纯净／二分会话](../core/test_session.py)。 | 二分结果只是候选，不承诺复现只在正式档发生的问题。已有能力不应作为「待补齐」重复立项；本次不验收并行修改中的存档编辑 UI。 |
| 通用部署与资源覆盖器 | **建议暂不做。** 所查路径管理的是原生模组目录、工坊副本和存档里的加载顺序，没有证据表明项目有 MO2 式虚拟层、Vortex 式部署规则，或能为跨模组资源选择运行时胜者。 | 在核实游戏的解析和覆盖规则、看到真实需求前，不做虚拟部署、合并补丁或跨管理器格式导入。 |

## 开发顺序与验收标准

1. **先做前置补齐与写前复核。** 在现有顺序／预设预览里按「已装未启用、未装且来源已核对、来源不明、依赖成环」分组。可解析的已装项生成一份递归启用及重排草稿；缺项复用已知工坊入口或现有下载操作，不用相似名称猜 ID。刷新后重验，再单独确认应用。验收：`A → B → C`、缺失／歧义来源、循环、预览后目录或存档变化、下载取消均有明确结果；仅预览或下载失败不写存档，不自动授权或启动。把确知的阻断与版本／资源的提示分开，并在真正写入时复核。
2. **再做 PSMM 自有的可核验方案清单。** 保留 `PSMM1` 为轻量分享；新格式先记录 AppID、顺序、模组 ID、来源及有效副本的内容指纹。工坊 ID、时间戳和声明版本只作查找线索。导入先区分「内容匹配、同 ID 但内容不同、缺失、无法核验」，不能拿到原内容时如实说不能复原。验收：两台机器同 ID、同声明版本但内容不同也能提示；没有历史副本时不静默替换。默认不带配置；将来如允许选带 `ModConfigs`，应先预览并排除账号、路径、授权与档位数据，不默认再分发第三方模组文件。
3. **有实例再扩展排序和冲突解释。** 先显示前置链、资源所属模组和路径，以及同 ID 本地／工坊实际选用的来源；前置规则不足且已有可复现案例时，再试本地、可撤销的前后规则预览。验收：能指出循环涉及哪些规则；没有真实加载证据时，继续标注「重叠」，不用「覆盖」「已解决」。社区规则库、任意历史下载、全局文件部署和自动授权不进入前两阶段。

## 取证与限制

- 先尝试 `web.run`，搜索和打开官方页面均未返回可引用结果。随后以只读 HTTPS 请求获取官方 GitHub 仓库的文件树及固定提交的指南或源码，下列证据正文均取得 HTTP 200。Nexus Mods 的 `Managing_File_Conflicts` Wiki 页面回 HTTP 403，改用 Nexus Mods 自有仓库的冲突和规则源码；MO2 仓库下猜测的 `master/README.md` 路径回 HTTP 404，改用同一官方仓库中已核实存在的源码。失败页面不作为证据。
- 固定提交只为复核同一份材料，不代表发布版本，也未安装外部管理器。PSMM 只做静态源码抽查，未运行测试、构建、真实游戏、Steam 下载、档位恢复或跨机互换；未验证行为不算已验收。

[mo2-vfs]: https://github.com/ModOrganizer2/modorganizer/blob/4ba22f0d8b6f9e08e44008b8395cd774551b4ebe/src/usvfsconnector.cpp#L216-L231
[mo2-profile]: https://github.com/ModOrganizer2/modorganizer/blob/4ba22f0d8b6f9e08e44008b8395cd774551b4ebe/src/profile.cpp#L129-L192
[mo2-conflict]: https://github.com/ModOrganizer2/modorganizer/blob/4ba22f0d8b6f9e08e44008b8395cd774551b4ebe/src/modinfodialogconflicts.cpp#L634-L683
[vortex-readme]: https://github.com/Nexus-Mods/Vortex/blob/116c8aba95ae2ef31dfdc7083eb909d611d3712a/README.md#L23-L30
[vortex-conflict]: https://github.com/Nexus-Mods/Vortex/blob/116c8aba95ae2ef31dfdc7083eb909d611d3712a/extensions/mod-dependency-manager/src/util/conflicts.ts#L98-L175
[vortex-rules]: https://github.com/Nexus-Mods/Vortex/blob/116c8aba95ae2ef31dfdc7083eb909d611d3712a/extensions/mod-dependency-manager/src/views/ConflictEditor.tsx#L78-L100
[vortex-deploy]: https://github.com/Nexus-Mods/Vortex/blob/116c8aba95ae2ef31dfdc7083eb909d611d3712a/src/renderer/src/extensions/hardlink_activator/index.ts#L289-L308
[rim-basic]: https://github.com/RimSort/RimSort/blob/bc7140893c41cf956fca97efaa440ed15661ae8e/docs/user-guide/basic-usage.md
[rim-sort]: https://github.com/RimSort/RimSort/blob/bc7140893c41cf956fca97efaa440ed15661ae8e/docs/user-guide/sorting-algorithms.md#L77-L88
[rim-rules]: https://github.com/RimSort/RimSort/blob/bc7140893c41cf956fca97efaa440ed15661ae8e/docs/user-guide/rule-editor.md#L34-L68
[rim-db]: https://github.com/RimSort/RimSort/blob/bc7140893c41cf956fca97efaa440ed15661ae8e/docs/user-guide/databases.md#L8-L44
[r2-readme]: https://github.com/ebkr/r2modmanPlus/blob/6d4ef0a858ddbb0658ca37d7ba824a635808d4cd/README.md#L20-L32
[r2-import]: https://github.com/ebkr/r2modmanPlus/blob/6d4ef0a858ddbb0658ca37d7ba824a635808d4cd/src/components/profiles-modals/ImportProfileModal.vue
[r2-deps]: https://github.com/ebkr/r2modmanPlus/blob/6d4ef0a858ddbb0658ca37d7ba824a635808d4cd/src/utils/DependencyUtils.ts#L39-L101
