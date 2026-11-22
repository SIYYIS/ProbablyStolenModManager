"""Read author docs and exact-match hints verified against shipped mod docs."""

from pathlib import Path

from .file_ops import is_link


KNOWN_HINTS = {
    ("watertower", "IMBA"): ("水塔产水算法", "0：共享空气；1：线性产水。", "模组清单"),
    ("customerstocking", "auto_confirm_sale"): ("自动成交", "开启后自动确认出售；关闭时只上货，由你检查后手动出售。作者文档标注默认关闭。", "作者 README"),
    ("customerstocking", "skip_machine_contents"): ("跳过机器内物品", "取货时跳过机器库存，不是排除普通储物箱。", "作者 README"),
    ("customerstocking", "skip_machine_modules_and_batteries"): ("跳过机器内的模组和电池", "只过滤机器库存，普通容器中的同类物品不受影响。", "作者 README"),
    ("customerstocking", "allow_mislabelled_goods"): ("允许标签不实商品", "开启后，标签不实的商品也可作为上货候选。", "作者 README"),
    ("item_details", "show_info_enhance"): ("物品信息补充", "信息补充的总开关。关闭后收起这组信息，但保留各子项设置；不影响悬停查看顾客对话。", "作者 README"),
    ("item_details", "show_container_contents"): ("容器内容物", "显示容器的内容物，属于物品信息补充组。", "作者 README"),
    ("item_details", "show_result_prefill"): ("鉴定结果预填", "预先勾选赝品问题并填好报价，不会自动提交。", "作者 README"),
}


def read_mod_readme(root: Path | None, folder: Path) -> dict[str, str] | None:
    """Only fixed filenames in the scanned directory. Never render as HTML."""
    limit = 64 * 1024
    try:
        if root is None or folder.parent != root or is_link(root) or is_link(folder):
            return None
        for name in ("README.zh-CN.md", "README_zh.md", "README.md", "README.txt", "使用说明.md", "玩法说明.md", "README_EN.md"):
            path = folder / name
            if is_link(path) or not path.is_file():
                continue
            with path.open("rb") as stream:
                data = stream.read(limit + 1)
            if b"\0" in data:
                continue
            text = data[:limit].decode("utf-8-sig", errors="replace").strip()
            if text:
                return {"name": path.name, "text": text + ("\n\n（预览已截断）" if len(data) > limit else "")}
    except OSError:
        pass
    return None
