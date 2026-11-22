# 模组维护：掉落表比较与文件体检

2026-11-22 核对 Steam 工坊指向的 [中文模组指南（固定版本）](https://github.com/dragonpan2/probably-stolen-modding/blob/3541332ad755a07e423077659863268256ccd233/ModdingGuide.zh-CN.md)。指南经开发者审阅，但提示实际行为以游戏为准；此功能未运行游戏验证，不代替游戏加载器或安全扫描器。

## 掉落表比较

- [JSON 数据格式与顺序](https://github.com/dragonpan2/probably-stolen-modding/blob/3541332ad755a07e423077659863268256ccd233/ModdingGuide.zh-CN.md#L637-L675)：只读取 `LootTables/` 直属 JSON（不递归）；按界面草稿顺序展示副本、文件名、表 / 表组、条目和权重。可比较 `tables`、`groups` 声明和 `patches` 中的 `clear`、`add`、`remove`、`setWeight`。一个文件按表、表组、补丁顺序；一个补丁的动作顺序是清空、添加、移除、设权重。指南说明文件按字母顺序读取；工具以文件名排序展示，不以文件排序的细节推断可自动合并的结果。
- `add` 对已存在的条目替换权重，权重并不是百分比。仅为[指南明确列出的原版物品表](https://github.com/dragonpan2/probably-stolen-modding/blob/3541332ad755a07e423077659863268256ccd233/ModdingGuide.zh-CN.md#L584-L615)中，由多个模组以 `add` 写入不同正权重的同一条目生成补丁。每项冲突都要明确选择来源；原先互不冲突的条目不会复制进补丁。同表存在有效 `clear`、`remove`、`setWeight`、重建声明、大小写别名或同来源重复写入冲突条目时只比较、不生成；表组和自定义表也只比较。输入有不明格式、缺失/无法确定唯一有效副本、重复 JSON 键、不可读文件或触及扫描上限时，不发布。
- [模组加载前提](https://github.com/dragonpan2/probably-stolen-modding/blob/3541332ad755a07e423077659863268256ccd233/ModdingGuide.zh-CN.md#L295-L305)：纯 JSON **不是可独立加载的模组**。工具只生成数据，不生成 DLL、清单或游戏批准。可复制 / 导出预览；如需安装，在草稿末位选已有本地载体，它必须有结构有效的程序集声明且没有其他直属掉落 JSON。确认后只新增 `LootTables/PSMM_Merged_Loot.json`，已有文件不覆盖。源副本、权重和载体在发布前重新核验。补丁假设目标表存在且载体在冲突模组之后生效；不能确认游戏内审批、最终应用顺序、其他 DLL 的运行时修改或物品 ID 实际存在。生成和发布均返回原来的 `draft_order`，不启用、不重排、不写存档。

## 模组文件体检

- [扫描与拒绝规则](https://github.com/dragonpan2/probably-stolen-modding/blob/3541332ad755a07e423077659863268256ccd233/ModdingGuide.zh-CN.md#L1431-L1443)：未在 `<Assemblies>` 中声明的 DLL 属于指南明示的阻断项（按模组根相对路径匹配）；可执行文件和脚本属于安全扫描对象；压缩包和美术源文件属于发布包提醒，不一概判定会被拒载。核查缺失 DLL、部分游戏自带程序集名和必要清单节点；不执行或反编译可疑文件，不判断 DLL 能力、完整依赖、授权或恶意性质。遵循本地跳过点开头的子目录，工坊只读。
- 本地单文件需再次确认才能移动到游戏目录下 `.PSMM-Quarantine/<记录 ID>/payload.bin`，不删除原始字节。记录先于移动写入，恢复校验原模组目录身份、清单内容、隔离文件哈希并拒绝覆盖；若原模组已换、目标被占用、路径含链接或隔离文件变动，文件保留在隔离区。不要把隔离目录移进 `Mods`。更大的可疑文件可报告，但达校验预算（每次体检最多 128 MiB）时不自动隔离。不能确认游戏已退出时拒绝写入。

## 集成与验证

- 在现有 `workspace` API router 上挂 `workspace.include_router(maintenance_router(ops))`，使用它已有的 `Operations.local()`、排错状态守卫和主应用的回环请求限制。不要直接挂到无守卫的 HTTP 服务。接口分组：`/api/maintenance/loot/{compare,generate,install}` 和 `/api/maintenance/health/{catalogue,scan,isolated,isolate,restore}`。
- 页面挂 `<MaintenanceTools order={order} onDraftOrder={setOrder} />`；可选回调只更新 UI 草稿，不应调用保存接口。安装补丁或隔离文件不会保存、启动或批准模组。
- 临时目录后端测试：`py -3.10 -B -m unittest discover -s tests -p test_maintenance.py`。独立界面测试：在 PowerShell 设置 `$env:PSMM_BROWSER_CHANNEL='msedge'` 后执行 `npm.cmd --prefix frontend run test:e2e -- --config=playwright.maintenance.config.ts`，仅本地夹具与模拟 HTTP。
